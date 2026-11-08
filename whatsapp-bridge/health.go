package main

// Connection health: catches the failures whatsmeow reports but the bridge used to ignore (keep-alive timeouts, outdated
// client, connect failures, stream errors), handles what can be handled (forced reconnect / restart), and exposes the
// problem in /api/status so the agent can alert the owner. Also keeps delivery/read receipts of what we sent and a log of
// incoming calls.

import (
	"fmt"
	"os"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

type healthState struct {
	Problem string `json:"problem"` // "" = healthy; keepalive | outdated | connect_failure | stream_error | banned
	Detail  string `json:"detail"`
	Since   string `json:"since"`
	Action  string `json:"action"` // what the bridge did about it
}

var (
	healthMu      sync.Mutex
	health        healthState
	lastReconnect time.Time
)

func setHealth(problem, detail, action string) {
	healthMu.Lock()
	if health.Problem != problem {
		health.Since = time.Now().Format(time.RFC3339)
	}
	health.Problem, health.Detail, health.Action = problem, detail, action
	healthMu.Unlock()
	logConn(fmt.Sprintf("%s: %s (%s)", problem, detail, action))
}

func clearHealth(reason string) {
	healthMu.Lock()
	had := health.Problem != ""
	health = healthState{}
	healthMu.Unlock()
	if had {
		logConn("healthy: " + reason)
	}
}

func healthSnapshot() healthState {
	healthMu.Lock()
	defer healthMu.Unlock()
	return health
}

// restartSoon exits so the run-bridge loop starts a fresh process (the reliable way to get a brand new connection).
func restartSoon(after time.Duration) {
	go func() { time.Sleep(after); os.Exit(1) }()
}

// reconnectAfterKeepAlive: whatsmeow keeps trying keep-alives forever on a dead socket; after 3 minutes without one
// answer force a reconnect (at most every 5 minutes), and restart the process if that did not help.
func reconnectAfterKeepAlive(client *whatsmeow.Client, lastSuccess time.Time) {
	if time.Since(lastSuccess) < 3*time.Minute {
		return
	}
	healthMu.Lock()
	if time.Since(lastReconnect) < 5*time.Minute {
		healthMu.Unlock()
		return
	}
	lastReconnect = time.Now()
	healthMu.Unlock()
	go func() {
		client.Disconnect()
		time.Sleep(2 * time.Second)
		if err := client.Connect(); err != nil {
			setHealth("keepalive", "reconnect failed: "+err.Error(), "restarting the bridge")
			restartSoon(5 * time.Second)
		}
	}()
}

func handleHealthEvent(client *whatsmeow.Client, evt interface{}) {
	switch v := evt.(type) {
	case *events.KeepAliveTimeout:
		mins := int(time.Since(v.LastSuccess).Minutes())
		setHealth("keepalive", fmt.Sprintf("no answer from WhatsApp for %d min (%d failed pings)", mins, v.ErrorCount), "reconnecting")
		reconnectAfterKeepAlive(client, v.LastSuccess)
	case *events.KeepAliveRestored:
		clearHealth("keep-alive answered again")
	case *events.Connected:
		clearHealth("connected")
	case *events.ClientOutdated:
		setHealth("outdated", "WhatsApp rejected this bridge version — the bridge (whatsmeow) must be updated", "waiting for an update")
	case *events.ConnectFailure:
		switch v.Reason {
		case events.ConnectFailureClientOutdated:
			setHealth("outdated", "WhatsApp rejected this bridge version (405) — update needed", "waiting for an update")
		case events.ConnectFailureTempBanned:
			setHealth("banned", "temporary ban (402): "+v.Message, "sending paused")
			pauseSends(client, 24*time.Hour, "WhatsApp: חסימה זמנית (402)")
		case events.ConnectFailureMainDeviceGone, events.ConnectFailureUnknownLogout, events.ConnectFailureLoggedOut:
			setHealth("logged_out", fmt.Sprintf("%d: %s", v.Reason, v.Message), "needs a new QR scan")
		default:
			setHealth("connect_failure", fmt.Sprintf("%d: %s", v.Reason, v.Message), "restarting the bridge in 1 min")
			restartSoon(time.Minute)
		}
	case *events.StreamError:
		setHealth("stream_error", "stream error "+v.Code, "restarting the bridge in 30 s")
		restartSoon(30 * time.Second)
	case *events.Receipt:
		storeReceipt(v)
	case *events.CallOffer:
		storeCall(v.From, v.CallID, v.Timestamp, "offer", "")
	case *events.CallAccept:
		storeCall(v.From, v.CallID, v.Timestamp, "accept", "")
	case *events.CallTerminate:
		storeCall(v.From, v.CallID, v.Timestamp, "terminate", v.Reason)
	}
}

func ensureHealthTables() {
	if globalStore == nil {
		return
	}
	globalStore.db.Exec(`CREATE TABLE IF NOT EXISTS receipts (message_id TEXT, chat_jid TEXT, reader TEXT, type TEXT, at TIMESTAMP,
		PRIMARY KEY (message_id, reader, type))`)
	globalStore.db.Exec(`CREATE TABLE IF NOT EXISTS calls (call_id TEXT, caller TEXT, event TEXT, reason TEXT, at TIMESTAMP,
		PRIMARY KEY (call_id, event))`)
}

// storeReceipt keeps delivered / read / played receipts for messages WE sent (send_log), so "did he read it?" has an answer.
func storeReceipt(r *events.Receipt) {
	if globalStore == nil || r.IsFromMe {
		return
	}
	kind := map[types.ReceiptType]string{types.ReceiptTypeDelivered: "delivered", types.ReceiptTypeRead: "read",
		types.ReceiptTypePlayed: "played"}[r.Type]
	if kind == "" {
		return
	}
	reader := normalizeUser(globalClient, r.Sender.User)
	for _, id := range r.MessageIDs {
		var n int
		globalStore.db.QueryRow("SELECT COUNT(*) FROM send_log WHERE message_id = ?", id).Scan(&n)
		if n == 0 {
			continue
		}
		globalStore.db.Exec("INSERT OR IGNORE INTO receipts (message_id, chat_jid, reader, type, at) VALUES (?, ?, ?, ?, ?)",
			id, r.Chat.String(), reader, kind, r.Timestamp.Format("2006-01-02 15:04:05"))
	}
}

func storeCall(from types.JID, id string, at time.Time, event, reason string) {
	if globalStore == nil {
		return
	}
	globalStore.db.Exec("INSERT OR IGNORE INTO calls (call_id, caller, event, reason, at) VALUES (?, ?, ?, ?, ?)",
		id, normalizeUser(globalClient, from.User), event, reason, at.Format("2006-01-02 15:04:05"))
}

func registerHealth(client *whatsmeow.Client) {
	ensureHealthTables()
	client.AddEventHandler(func(evt interface{}) { handleHealthEvent(client, evt) })
}

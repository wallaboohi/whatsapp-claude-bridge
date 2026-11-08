package main

// Extra REST endpoints for the control panel: health/status and the send policy.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

var bridgeStarted = time.Now()

// version is set at build time: go build -ldflags "-X main.version=1.2.3"
var version = "dev"

// connection bookkeeping for the control panel
var (
	connMu         sync.Mutex
	connectedSince time.Time
	lastDisconnect time.Time
	disconnects    int
)

func logConn(event string) {
	if globalStore != nil {
		globalStore.db.Exec("CREATE TABLE IF NOT EXISTS conn_log (at TIMESTAMP, event TEXT)")
		globalStore.db.Exec("INSERT INTO conn_log (at, event) VALUES (?, ?)", time.Now().Format("2006-01-02 15:04:05"), event)
	}
	publish(map[string]any{"type": "connection", "event": event, "at": time.Now().Format(time.RFC3339)})
}

func markConnected() {
	connMu.Lock()
	connectedSince = time.Now()
	connMu.Unlock()
	logConn("connected")
}

func markDisconnected() {
	connMu.Lock()
	lastDisconnect, connectedSince = time.Now(), time.Time{}
	disconnects++
	connMu.Unlock()
	logConn("disconnected")
}

func registerExtraAPI(client *whatsmeow.Client) {
	http.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		p := loadPolicy()
		var sentToday, draftsPending int
		if globalStore != nil {
			globalStore.db.QueryRow("SELECT COUNT(*) FROM send_log WHERE ok = 1 AND substr(timestamp,1,10) = date('now','localtime')").Scan(&sentToday)
			globalStore.db.QueryRow("SELECT COUNT(*) FROM drafts WHERE status = 'pending'").Scan(&draftsPending)
		}
		paused := false
		if t, err := time.Parse(time.RFC3339, p.PausedUntil); err == nil && time.Now().Before(t) {
			paused = true
		}
		connMu.Lock()
		since, lastDisc, discs := connectedSince, lastDisconnect, disconnects
		connMu.Unlock()
		fmtT := func(t time.Time) string {
			if t.IsZero() {
				return ""
			}
			return t.Format(time.RFC3339)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"version":         version,
			"connected_since": fmtT(since),
			"last_disconnect": fmtT(lastDisc),
			"disconnects":     discs,
			"connected":       client.IsConnected(),
			"logged_in":       client.IsLoggedIn(),
			"started":         bridgeStarted.Format(time.RFC3339),
			"policy":          p,
			"paused":          paused,
			"sent_today":      sentToday,
			"drafts_pending":  draftsPending,
			"daily_cap":       envInt("WA_DAILY_CAP", 150),
			"health":          healthSnapshot(),
		})
	})

	// POST a partial policy: {"mode":"auto","auto_until":"..."} / {"paused_until":""} / {"allow":[...]}
	http.HandleFunc("/api/policy", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			policyMu.Lock()
			p := loadPolicy()
			if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
				policyMu.Unlock()
				http.Error(w, "bad json", http.StatusBadRequest)
				return
			}
			p.Mode, p.AutoUntil = "draft", "" // no auto mode (owner's rule); "allow" stays for rule 4
			savePolicy(p)
			policyMu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(loadPolicy())
	})

	// {"name":"Claude","participants":[],"description":"..."} → {"jid":"...@g.us"}. With no participants the group holds
	// only this account — the setup wizard uses it to create the owner's private control group.
	http.HandleFunc("/api/create_group", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Name         string   `json:"name"`
			Participants []string `json:"participants"`
			Description  string   `json:"description"`
		}
		if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "POST json", http.StatusBadRequest)
			return
		}
		name, err := groupName(req.Name)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		jids := make([]types.JID, 0, len(req.Participants))
		for _, p := range req.Participants {
			jid := types.NewJID(p, types.DefaultUserServer)
			if strings.Contains(p, "@") {
				if jid, err = types.ParseJID(p); err != nil {
					http.Error(w, "bad participant "+p, http.StatusBadRequest)
					return
				}
			}
			jids = append(jids, jid)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		w.Header().Set("Content-Type", "application/json")
		info, err := client.CreateGroup(ctx, whatsmeow.ReqCreateGroup{Name: name, Participants: jids})
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			json.NewEncoder(w).Encode(map[string]any{"error": err.Error()})
			return
		}
		res := map[string]any{"jid": info.JID.String(), "name": info.Name}
		if req.Description != "" {
			if err := client.SetGroupDescription(ctx, info.JID, req.Description); err != nil {
				res["description_error"] = err.Error()
			}
		}
		json.NewEncoder(w).Encode(res)
	})

	// {"jid":"...@g.us"} — live group name from WhatsApp; also heals the stored chat name if it drifted (agent's daily check)
	http.HandleFunc("/api/group_info", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			JID string `json:"jid"`
		}
		if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "POST json", http.StatusBadRequest)
			return
		}
		jid, err := types.ParseJID(req.JID)
		if err != nil || jid.Server != types.GroupServer {
			http.Error(w, "bad group jid", http.StatusBadRequest)
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		res := map[string]any{"success": false}
		info, err := client.GetGroupInfo(ctx, jid)
		if err != nil {
			res["error"] = err.Error()
		} else {
			res["success"] = true
			res["name"] = info.Name
			res["description"] = info.Topic
			res["members"] = len(info.Participants)
			res["stored_name"] = ""
			res["healed"] = false
			if globalStore != nil && info.Name != "" {
				var stored string
				globalStore.db.QueryRow("SELECT name FROM chats WHERE jid = ?", jid.String()).Scan(&stored)
				res["stored_name"] = stored
				if stored != info.Name {
					globalStore.db.Exec("UPDATE chats SET name = ? WHERE jid = ?", info.Name, jid.String())
					res["healed"] = true
				}
			}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(res)
	})

	// {"jid":"...@g.us","name":"...","description":"..."} — rename a group and/or set its description (needs admin rights)
	http.HandleFunc("/api/set_group", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			JID         string  `json:"jid"`
			Name        *string `json:"name"`
			Description *string `json:"description"`
		}
		if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "POST json", http.StatusBadRequest)
			return
		}
		jid, err := types.ParseJID(req.JID)
		if err != nil || jid.Server != types.GroupServer {
			http.Error(w, "bad group jid", http.StatusBadRequest)
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		res := map[string]any{"success": true}
		if req.Name != nil {
			name, nerr := groupName(*req.Name)
			if nerr == nil {
				nerr = client.SetGroupName(ctx, jid, name)
			}
			if nerr != nil {
				res["success"], res["name_error"] = false, nerr.Error()
			} else if globalStore != nil {
				globalStore.db.Exec("UPDATE chats SET name = ? WHERE jid = ?", name, jid.String())
			}
		}
		if req.Description != nil {
			derr := textOK(*req.Description)
			if derr == nil {
				derr = client.SetGroupDescription(ctx, jid, *req.Description)
			}
			if derr != nil {
				res["success"], res["description_error"] = false, derr.Error()
			}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(res)
	})

	registerAdminAPI(client)

	// {"jid":"...@g.us"} — leave a group (refuses the control group)
	http.HandleFunc("/api/leave_group", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			JID string `json:"jid"`
		}
		if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "POST json", http.StatusBadRequest)
			return
		}
		jid, err := types.ParseJID(req.JID)
		if err != nil || jid.Server != types.GroupServer {
			http.Error(w, "bad group jid", http.StatusBadRequest)
			return
		}
		if lp := loadPolicy(); req.JID == lp.ControlGroup || inList(lp.ControlGroups, jid) {
			http.Error(w, "refusing to leave a control group", http.StatusForbidden)
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		err = client.LeaveGroup(ctx, jid)
		w.Header().Set("Content-Type", "application/json")
		res := map[string]any{"success": err == nil}
		if err != nil {
			res["error"] = err.Error()
		}
		json.NewEncoder(w).Encode(res)
	})
}

// groupName trims the name and enforces WhatsApp's 25-character limit (longer names fail with 406).
func groupName(s string) (string, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		s = "Claude"
	}
	if err := textOK(s); err != nil {
		return "", err
	}
	if utf8.RuneCountInString(s) > 100 { // WhatsApp allows 100 characters for a group subject
		return "", fmt.Errorf("group name longer than 100 characters")
	}
	return s, nil
}

// textOK refuses text that was mangled on its way in (console code page turns Hebrew into "????" / U+FFFD).
func textOK(s string) error {
	if !utf8.ValidString(s) || strings.ContainsRune(s, '�') || strings.Contains(s, "??") {
		return fmt.Errorf("text looks corrupted (invalid UTF-8 / U+FFFD / '??') - send it as UTF-8 JSON from a file, never via a shell command line")
	}
	return nil
}

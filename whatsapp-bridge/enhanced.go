package main

// Enhancements over the upstream bridge:
//   - outgoing messages sent through the API are stored like incoming ones
//   - media is downloaded to disk as soon as it arrives, so it never expires on the server
//   - polls: questions, options and decrypted votes are stored (tables polls / poll_votes)
//   - anti-ban send pacing: global + per-recipient spacing with jitter, typing presence, daily caps

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"strconv"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func contextBG() context.Context { return context.Background() }

// globalStore lets the send path record outgoing messages without changing upstream signatures.
var globalStore *MessageStore

func initEnhancedSchema(store *MessageStore) error {
	_, err := store.db.Exec(`
		CREATE TABLE IF NOT EXISTS polls (
			poll_id TEXT,
			chat_jid TEXT,
			creator TEXT,
			question TEXT,
			options TEXT,
			selectable INTEGER,
			timestamp TIMESTAMP,
			PRIMARY KEY (poll_id, chat_jid)
		);
		CREATE TABLE IF NOT EXISTS poll_votes (
			poll_id TEXT,
			chat_jid TEXT,
			voter TEXT,
			options TEXT,
			timestamp TIMESTAMP,
			PRIMARY KEY (poll_id, chat_jid, voter)
		);
		CREATE TABLE IF NOT EXISTS drafts (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			recipient TEXT,
			name TEXT,
			message TEXT,
			media_path TEXT,
			created TIMESTAMP,
			status TEXT
		);
		CREATE TABLE IF NOT EXISTS send_log (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			recipient TEXT,
			message_id TEXT,
			ok BOOLEAN,
			detail TEXT,
			timestamp TIMESTAMP
		);
	`)
	if err != nil {
		return err
	}
	// ✅-approval support (ignore "duplicate column" errors on an already migrated db)
	store.db.Exec("ALTER TABLE drafts ADD COLUMN card_id TEXT")
	store.db.Exec("ALTER TABLE drafts ADD COLUMN quoted_id TEXT")
	// special drafts (poll / contact / forward): ✅ rebuilds and sends the real message, not its text description
	store.db.Exec("ALTER TABLE drafts ADD COLUMN kind TEXT")
	store.db.Exec("ALTER TABLE drafts ADD COLUMN payload TEXT")
	return nil
}

// ---------- polls ----------

func pollCreation(msg *waProto.Message) *waProto.PollCreationMessage {
	for _, p := range []*waProto.PollCreationMessage{
		msg.GetPollCreationMessage(), msg.GetPollCreationMessageV2(), msg.GetPollCreationMessageV3(),
		msg.GetPollCreationMessageV5(), msg.GetPollCreationMessageV6(),
	} {
		if p != nil {
			return p
		}
	}
	// owner 11.11: the weekly lodging poll in the segment group never reached the database — newer clients send
	// pollCreationMessageV4, a FutureProofMessage that wraps the real poll message
	if fp := msg.GetPollCreationMessageV4(); fp != nil && fp.GetMessage() != nil {
		return pollCreation(fp.GetMessage())
	}
	return nil
}

// handlePolls stores poll questions and votes; returns true when the event was a poll message.
func handlePolls(client *whatsmeow.Client, store *MessageStore, msg *events.Message, logger waLog.Logger) bool {
	chatJID := msg.Info.Chat.String()
	if p := pollCreation(msg.Message); p != nil {
		var opts []string
		for _, o := range p.GetOptions() {
			opts = append(opts, o.GetOptionName())
		}
		js, _ := json.Marshal(opts)
		_, err := store.db.Exec(`INSERT OR REPLACE INTO polls (poll_id, chat_jid, creator, question, options, selectable, timestamp)
			VALUES (?, ?, ?, ?, ?, ?, ?)`, msg.Info.ID, chatJID, msg.Info.Sender.User, p.GetName(), string(js),
			p.GetSelectableOptionsCount(), msg.Info.Timestamp)
		if err != nil {
			logger.Warnf("Failed to store poll: %v", err)
		}
		// keep a readable trace in the normal message stream as well
		store.StoreMessage(msg.Info.ID, chatJID, msg.Info.Sender.User, "📊 "+p.GetName()+" | "+string(js),
			msg.Info.Timestamp, msg.Info.IsFromMe, "poll", "", "", nil, nil, nil, 0)
		return true
	}
	if upd := msg.Message.GetPollUpdateMessage(); upd != nil {
		pollID := upd.GetPollCreationMessageKey().GetID()
		vote, err := client.DecryptPollVote(context.Background(), msg)
		if err != nil {
			logger.Warnf("Failed to decrypt poll vote: %v", err)
			return true
		}
		var optsJSON string
		store.db.QueryRow("SELECT options FROM polls WHERE poll_id = ?", pollID).Scan(&optsJSON)
		var names []string
		json.Unmarshal([]byte(optsJSON), &names)
		byHash := map[string]string{}
		for _, n := range names {
			h := sha256.Sum256([]byte(n))
			byHash[string(h[:])] = n
		}
		var chosen []string
		for _, h := range vote.GetSelectedOptions() {
			if n, ok := byHash[string(h)]; ok {
				chosen = append(chosen, n)
			} else {
				chosen = append(chosen, fmt.Sprintf("?%x", h[:4]))
			}
		}
		js, _ := json.Marshal(chosen)
		_, err = store.db.Exec(`INSERT OR REPLACE INTO poll_votes (poll_id, chat_jid, voter, options, timestamp) VALUES (?, ?, ?, ?, ?)`,
			pollID, chatJID, msg.Info.Sender.User, string(js), msg.Info.Timestamp)
		if err != nil {
			logger.Warnf("Failed to store poll vote: %v", err)
		}
		return true
	}
	return false
}

// ---------- media ----------

var mediaSlots = make(chan struct{}, 2) // at most two background downloads at a time

// autoDownload saves incoming media to disk right away (store/<chat>/<file>) so it survives server expiry.
func autoDownload(client *whatsmeow.Client, store *MessageStore, messageID, chatJID string) {
	go func() {
		mediaSlots <- struct{}{}
		defer func() { <-mediaSlots }()
		time.Sleep(time.Duration(1+rand.Intn(3)) * time.Second)
		if ok, _, _, path, err := downloadMedia(client, store, messageID, chatJID); !ok || err != nil {
			fmt.Printf("Auto-download failed for %s: %v\n", messageID, err)
		} else {
			fmt.Printf("Auto-saved media %s\n", path)
		}
	}()
}

// ---------- outgoing ----------

func recordSent(recipient types.JID, id, content, mediaType, filename string, ok bool, detail string) {
	if globalStore == nil {
		return
	}
	now := time.Now()
	globalStore.db.Exec(`INSERT INTO send_log (recipient, message_id, ok, detail, timestamp) VALUES (?, ?, ?, ?, ?)`,
		recipient.String(), id, ok, detail, now)
	if ok {
		// keep the existing chat name; only create the row if this chat is new
		globalStore.db.Exec(`INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
			ON CONFLICT(jid) DO UPDATE SET last_message_time = excluded.last_message_time`, recipient.String(), recipient.User, now)
		globalStore.StoreMessage(id, recipient.String(), "me", content, now, true, mediaType, filename, "", nil, nil, nil, 0)
	}
}

// ---------- pacing (anti-ban) ----------

type pacer struct {
	mu       sync.Mutex
	last     time.Time
	lastTo   map[string]time.Time
	day      string
	sentDay  int
	newToday map[string]bool
}

var sendPacer = &pacer{lastTo: map[string]time.Time{}, newToday: map[string]bool{}}

func envInt(name string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(name)); err == nil {
		return v
	}
	return def
}

// knownChat reports whether we already have conversation history with this JID.
func knownChat(jid string) bool {
	if globalStore == nil {
		return true
	}
	var n int
	globalStore.db.QueryRow("SELECT COUNT(*) FROM messages WHERE chat_jid = ? AND is_from_me = 0", jid).Scan(&n)
	return n > 0
}

// todaySends rebuilds today's counters from send_log, so a bridge restart does not reset the daily caps.
// Only sends to OTHER people count (the owner's chats and control groups are exempt).
func todaySends() (int, map[string]bool) {
	n, fresh := 0, map[string]bool{}
	if globalStore == nil {
		return n, fresh
	}
	rows, err := globalStore.db.Query("SELECT recipient FROM send_log WHERE ok = 1 AND substr(timestamp,1,10) = date('now','localtime')")
	if err != nil {
		return n, fresh
	}
	var recips []string
	for rows.Next() {
		var r string
		if rows.Scan(&r) == nil {
			recips = append(recips, r)
		}
	}
	rows.Close()
	for _, r := range recips {
		j, err := types.ParseJID(r)
		if err != nil || isOwnTarget(j) {
			continue
		}
		n++
		if !fresh[r] && !knownChat(r) {
			fresh[r] = true
		}
	}
	return n, fresh
}

// wait blocks until it is reasonable to send to this recipient, or returns an error when a daily cap is hit.
// Defaults: 3–7 s between any two sends, 20 s between sends to the same chat, 150 sends/day,
// 15/day to chats that never wrote to us. Override with WA_* environment variables.
// The owner's own chats and the control groups are exempt from the caps, the same-chat gap and "typing"
// (replies, draft cards and alerts must never be blocked by a busy day); they keep only the short global gap.
func (p *pacer) wait(client *whatsmeow.Client, jid types.JID, textLen int) error {
	own := isOwnTarget(jid)
	p.mu.Lock()
	defer p.mu.Unlock()
	today := time.Now().Format("2006-01-02")
	if p.day != today {
		p.day = today
		p.sentDay, p.newToday = todaySends()
	}
	key := jid.String()
	if !own {
		if p.sentDay >= envInt("WA_DAILY_CAP", 150) {
			return fmt.Errorf("daily send cap reached (%d)", p.sentDay)
		}
		if !knownChat(key) && !p.newToday[key] {
			if len(p.newToday) >= envInt("WA_NEW_CHATS_PER_DAY", 15) {
				return fmt.Errorf("daily cap for new chats reached")
			}
			p.newToday[key] = true
		}
	}
	gap := time.Duration(envInt("WA_MIN_GAP_MS", 3000)+rand.Intn(envInt("WA_JITTER_MS", 4000))) * time.Millisecond
	if d := time.Until(p.last.Add(gap)); d > 0 {
		time.Sleep(d)
	}
	if d := time.Until(p.lastTo[key].Add(time.Duration(envInt("WA_SAME_CHAT_GAP_MS", 20000)) * time.Millisecond)); !own && d > 0 && jid.Server != types.GroupServer {
		time.Sleep(d)
	}
	// look human: show "typing…" for a moment proportional to the text length
	if textLen > 0 && !own {
		client.SendChatPresence(context.Background(), jid, types.ChatPresenceComposing, types.ChatPresenceMediaText)
		typing := time.Duration(min(800+textLen*25, 4000)) * time.Millisecond
		time.Sleep(typing)
		client.SendChatPresence(context.Background(), jid, types.ChatPresencePaused, types.ChatPresenceMediaText)
	}
	p.last = time.Now()
	p.lastTo[key] = p.last
	if !own {
		p.sentDay++
	}
	return nil
}

package main

// Send policy and account-health guard.
//
// Draft mode (default): the bridge only sends to the owner's own chats. A send to anyone else becomes a
// draft card in the owner's control group with a wa.me link that opens that chat with the text pre-typed,
// so the owner reviews and presses send. Auto mode (explicit permission, optionally time-limited) sends directly.
//
// Health guard: a temporary ban, a reach-out/capping error or a rate-limit answer pauses every send to
// other people, so the tool backs off instead of digging the account deeper.

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

const policyFile = "store/send_policy.json"

type SendPolicy struct {
	Mode          string   `json:"mode"`           // "draft" or "auto"
	AutoUntil     string   `json:"auto_until"`     // RFC3339; auto mode falls back to draft after this
	Owner         []string `json:"owner"`          // the owner's own chats: always sent directly
	ControlGroup  string   `json:"control_group"`  // where draft cards and alerts go
	ControlGroups []string `json:"control_groups"` // extra groups the owner talks to Claude in (connected from the dashboard): replies go out directly
	Allow         []string `json:"allow"`          // extra recipients that may always be sent to directly
	PausedUntil   string   `json:"paused_until"`   // set by the health guard
	PauseReason   string   `json:"pause_reason"`
}

var policyMu sync.Mutex

func defaultPolicy() SendPolicy {
	p := SendPolicy{Mode: "draft", ControlGroup: os.Getenv("WA_CONTROL_GROUP")}
	if o := os.Getenv("WA_OWNER"); o != "" {
		p.Owner = strings.Split(o, ",")
	}
	if p.ControlGroup == "" && globalClient != nil && globalClient.Store.ID != nil {
		p.ControlGroup = globalClient.Store.ID.ToNonAD().String() // the owner's chat with himself
	}
	return p
}

// isOwnChat: the account's own chat (by phone or LID) always counts as the owner.
func isOwnChat(jid types.JID) bool {
	if globalClient == nil || globalClient.Store.ID == nil {
		return false
	}
	return jid.User == globalClient.Store.ID.User || (!globalClient.Store.LID.IsEmpty() && jid.User == globalClient.Store.LID.User)
}

func loadPolicy() SendPolicy {
	p := defaultPolicy()
	if b, err := os.ReadFile(policyFile); err == nil {
		json.Unmarshal(b, &p)
	}
	return p
}

func savePolicy(p SendPolicy) {
	b, _ := json.MarshalIndent(p, "", "  ")
	os.WriteFile(policyFile, b, 0644)
}

func inList(list []string, jid types.JID) bool {
	for _, s := range list {
		if s == jid.String() || s == jid.User {
			return true
		}
	}
	return false
}

// ownIn: the owner's own chats and the control groups (where Claude talks to the owner).
func ownIn(p SendPolicy, jid types.JID) bool {
	return inList(p.Owner, jid) || jid.String() == p.ControlGroup || inList(p.ControlGroups, jid) || isOwnChat(jid)
}

// isOwnTarget: ownIn with the current policy file (takes policyMu — do not call while holding it).
func isOwnTarget(jid types.JID) bool {
	policyMu.Lock()
	defer policyMu.Unlock()
	return ownIn(loadPolicy(), jid)
}

// checkPolicy decides how a send to jid is handled: "send", "draft", or "paused".
func checkPolicy(jid types.JID) (string, SendPolicy) { return checkPolicyApproved(jid, false) }

// checkPolicyApproved: approved=true only for the single send of a draft the owner approved (✅ / dashboard / "מאשר לך
// שליחה"). "allow" holds a recipient approved in words (CLAUDE.md rule 4); there is no auto mode ("mode"/"auto_until"
// are ignored — the owner's rule). A pause always wins.
func checkPolicyApproved(jid types.JID, approved bool) (string, SendPolicy) {
	policyMu.Lock()
	defer policyMu.Unlock()
	p := loadPolicy()
	if ownIn(p, jid) {
		return "send", p
	}
	if t, err := time.Parse(time.RFC3339, p.PausedUntil); err == nil && time.Now().Before(t) {
		return "paused", p
	}
	if approved {
		return "send", p
	}
	if inList(p.Allow, jid) { // one recipient the owner approved in words ("מאשר לך שליחה" — CLAUDE.md rule 4)
		return "send", p
	}
	return "draft", p
}

// pauseSends stops all sends to other people for d and alerts the owner.
func pauseSends(client *whatsmeow.Client, d time.Duration, reason string) {
	policyMu.Lock()
	p := loadPolicy()
	until := time.Now().Add(d)
	if t, err := time.Parse(time.RFC3339, p.PausedUntil); err == nil && t.After(until) {
		until = t
	}
	p.PausedUntil, p.PauseReason = until.Format(time.RFC3339), reason
	savePolicy(p)
	policyMu.Unlock()
	fmt.Printf("SENDS PAUSED until %s: %s\n", until.Format("2006-01-02 15:04"), reason)
	if client != nil && client.IsConnected() {
		go sendWhatsAppMessage(client, p.ControlGroup, fmt.Sprintf("🤖 ⛔ שליחה לאחרים הושהתה עד %s.\nסיבה: %s\nהשיחה איתך ממשיכה כרגיל.",
			until.Format("02.01 15:04"), reason), "")
	}
}

// blockSendCodes: send-ack error codes WhatsApp uses when it throttles or restricts the ACCOUNT (not one chat):
// 429 rate limit, 463 reach-out time-lock (too many messages to new contacts), 475 account restricted.
var blockSendCodes = map[int]bool{429: true, 463: true, 475: true}

// looksLikeBlock recognises send errors that mean WhatsApp is throttling or restricting the account. Ordinary
// per-chat errors (a contact blocked us, we are not in that group, forbidden / not-authorized for one chat, timeouts)
// do NOT count — they used to pause every send for 6 hours.
func looksLikeBlock(err error) bool {
	if err == nil {
		return false
	}
	var iq *whatsmeow.IQError
	if errors.As(err, &iq) {
		return iq.Code == 429 || iq.Text == "rate-overlimit"
	}
	s := strings.ToLower(err.Error())
	if strings.Contains(s, "rate-overlimit") || strings.Contains(s, "rate overlimit") || strings.Contains(s, "too many requests") {
		return true
	}
	// "server returned error 463" (whatsmeow.ErrServerReturnedError + code)
	if errors.Is(err, whatsmeow.ErrServerReturnedError) || strings.Contains(s, "server returned error") {
		if m := serverCodeRe.FindStringSubmatch(s); m != nil {
			code, _ := strconv.Atoi(m[1])
			return blockSendCodes[code]
		}
	}
	return false
}

var serverCodeRe = regexp.MustCompile(`server returned error (\d+)`)

// draftCardText is the draft card posted in the control group. The approval hint comes first, so it is the first
// thing seen on a phone: long-press this message → ✅ (or 👍) sends exactly this one message; ❌ cancels.
func draftCardText(draftID int64, name string, jid types.JID, message, mediaPath string) string {
	// Owner layout (09.11): the text to approve first, then the approval hint, and the long wa.me link last (WhatsApp
	// folds it under "read more"). No draft number on the card; draftID stays in the drafts table.
	_ = draftID
	var card strings.Builder
	card.WriteString(fmt.Sprintf("🤖 📝 *טיוטה → %s*\n", name))
	card.WriteString("━━━━━━━━━━\n" + message + "\n━━━━━━━━━━\n")
	card.WriteString("👆 *לחיצה ארוכה ← ✅ לאישור* (הודעה אחת, לנמען הזה בלבד) · ❌ לביטול\n")
	card.WriteString("🕗 או השב לכרטיס עם שעה (08:00) — שליחה מתוזמנת")
	if mediaPath != "" {
		card.WriteString("\n📎 הקובץ המצורף מוצג כאן בהודעה נפרדת.")
	}
	if jid.Server == types.DefaultUserServer {
		// %20 instead of '+' (safer in wa.me); long links get cut by WhatsApp's link detection, so skip them
		if !needsCopyText(jid, message, mediaPath) {
			card.WriteString("\n\n🔗 לשליחה בעצמך (פותח את השיחה עם הטקסט מוכן):\n")
			card.WriteString(waLink(jid, message))
		} else {
			// owner 10.11: WhatsApp on the phone cuts a tapped link at ~400 characters (≈60 Hebrew letters), so the
			// pre-typed text arrived cut. Long texts: the full text comes as the next message, to copy and paste.
			card.WriteString("\n\n📋 הטקסט המלא להעתקה בהודעה הבאה (לחיצה ארוכה ← העתק). השיחה: https://wa.me/" + jid.User)
		}
	} else {
		card.WriteString("\n📋 הטקסט המלא להעתקה בהודעה הבאה (לחיצה ארוכה ← העתק)")
	}
	return card.String()
}

// waLinkMax: the longest wa.me?text= link WhatsApp on the phone opens whole (measured 10.11: cut at ~414 chars).
const waLinkMax = 380

func waLink(jid types.JID, message string) string {
	// %20 instead of '+' (safer in wa.me)
	return "https://wa.me/" + jid.User + "?text=" + strings.ReplaceAll(url.QueryEscape(message), "+", "%20")
}

// needsCopyText: no usable pre-typed link on the card (too long, a file, or a group) -> the text is posted separately.
func needsCopyText(jid types.JID, message, mediaPath string) bool {
	return jid.Server != types.DefaultUserServer || mediaPath != "" || len(waLink(jid, message)) > waLinkMax
}

// makeDraft turns a send into a draft card in the control group instead of sending it.
func makeDraft(client *whatsmeow.Client, p SendPolicy, jid types.JID, message, mediaPath, quotedID string) (bool, string) {
	return makeDraftKind(client, p, jid, message, mediaPath, quotedID, "", "")
}

// makeDraftKind: like makeDraft, for special messages — kind ("poll" / "contact" / "forward") + JSON payload are stored
// so the approval sends the real thing.
func makeDraftKind(client *whatsmeow.Client, p SendPolicy, jid types.JID, message, mediaPath, quotedID, kind, payload string) (bool, string) {
	name := jid.User
	if globalStore != nil {
		var n string
		globalStore.db.QueryRow("SELECT name FROM chats WHERE jid = ?", jid.String()).Scan(&n)
		if n != "" && n != jid.User {
			name = n
		}
	}
	if c, err := client.Store.Contacts.GetContact(contextBG(), jid); err == nil && c.FullName != "" {
		name = c.FullName
	}
	var draftID int64
	if globalStore != nil {
		if r, err := globalStore.db.Exec(`INSERT INTO drafts (recipient, name, message, media_path, created, status, quoted_id, kind, payload) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)`,
			jid.String(), name, message, mediaPath, time.Now(), quotedID, kind, payload); err == nil {
			draftID, _ = r.LastInsertId()
		}
	}
	ok, res := sendWhatsAppMessage(client, p.ControlGroup, draftCardText(draftID, name, jid, message, mediaPath), "")
	if ok && draftID > 0 && globalStore != nil {
		// remember the card's message id (from this send's own result) so a ✅ on it approves exactly this draft
		if cardID := sentID(res); cardID != "" {
			globalStore.db.Exec("UPDATE drafts SET card_id = ? WHERE id = ?", cardID, draftID)
		}
	}
	if ok && mediaPath != "" {
		sendWhatsAppMessage(client, p.ControlGroup, "🤖 📎 קובץ לטיוטה → "+name, mediaPath)
	}
	if ok && kind == "" && strings.TrimSpace(message) != "" && needsCopyText(jid, message, mediaPath) {
		// the exact text alone (no prefix), so long-press → copy gives just the message. The agent ignores it because
		// it equals a pending draft's text (agent.py is_draft_copy).
		sendWhatsAppMessage(client, p.ControlGroup, message, "")
	}
	if !ok {
		return false, "Draft could not be posted: " + res
	}
	return false, fmt.Sprintf("DRAFT: not sent — draft for %s posted to the control group for approval", name)
}

var sentIDRe = regexp.MustCompile(`id=([A-Za-z0-9]+)`)

// sentID extracts the message id from sendWhatsAppMessage's result ("Message sent to X id=3EB0…").
func sentID(res string) string {
	if m := sentIDRe.FindStringSubmatch(res); m != nil {
		return m[1]
	}
	return ""
}

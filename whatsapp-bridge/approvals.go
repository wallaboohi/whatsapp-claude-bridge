package main

// Draft approval by reaction: the owner reacts ✅ on a draft card in the control group and exactly that one draft
// is sent (one approval = one message to one recipient); ❌ cancels it. Anything else is ignored.

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"go.mau.fi/whatsmeow/proto/waE2E"
)

// handleReaction returns true if the event was a reaction (handled or not).
func handleReaction(client *whatsmeow.Client, msg *events.Message) bool {
	r := msg.Message.GetReactionMessage()
	if r == nil {
		return false
	}
	if !msg.Info.IsFromMe && globalStore != nil {
		storeApprovalReaction(client, msg, r)
		return true
	}
	if !msg.Info.IsFromMe || globalStore == nil { // only the owner's own reactions count for draft cards
		return true
	}
	chatJID := normalizeChat(client, msg.Info.Chat).String()
	if r.GetText() == readAloudEmoji { // owner 11.11: any message in the chat, his own too
		grantReadAloud(chatJID, r.GetKey().GetID()) // one voice note into this chat may skip the draft (readaloud.go)
	}
	publish(map[string]any{"type": "reaction", "chat_jid": chatJID,
		"message_id": r.GetKey().GetID(), "emoji": r.GetText()})
	policyMu.Lock()
	pol := loadPolicy()
	policyMu.Unlock()
	control := msg.Info.Chat.String()
	if pol.cardChatFor(control) != control {
		return true
	}
	emoji := r.GetText()
	if emoji != "✅" && emoji != "✔️" && emoji != "👍" && emoji != "❌" {
		return true
	}
	cardID := r.GetKey().GetID()
	var id int64
	if err := globalStore.db.QueryRow(`SELECT id FROM drafts WHERE card_id = ? AND status = 'pending'`, cardID).Scan(&id); err != nil {
		return true // not a pending draft card
	}
	var cardChat string
	globalStore.db.QueryRow(`SELECT COALESCE(card_chat,'') FROM drafts WHERE id = ?`, id).Scan(&cardChat)
	if (cardChat == "" && control != pol.ControlGroup) || (cardChat != "" && cardChat != control) {
		return true // the card lives in another group: only a ✅ there approves it
	}
	decideDraft(client, id, emoji != "❌", control)
	return true
}

// decideDraft sends (approve) or cancels exactly one pending draft: one approval = one message to one recipient.
func decideDraft(client *whatsmeow.Client, id int64, approve bool, control string) (string, error) {
	var recipient, name, message, media, quoted, kind, payload string
	err := globalStore.db.QueryRow(`SELECT recipient, name, message, COALESCE(media_path,''), COALESCE(quoted_id,''),
		COALESCE(kind,''), COALESCE(payload,'')
		FROM drafts WHERE id = ? AND status = 'pending'`, id).Scan(&recipient, &name, &message, &media, &quoted, &kind, &payload)
	if err != nil {
		return "", fmt.Errorf("draft %d is not pending", id)
	}
	if !approve {
		globalStore.db.Exec("UPDATE drafts SET status = 'cancelled' WHERE id = ? AND status = 'pending'", id)
		go sendWhatsAppMessage(client, control, "🤖 _הטיוטה ל"+name+" בוטלה._", "")
		return "cancelled", nil
	}
	// claim the draft atomically so a repeated approval can never send twice
	res, err := globalStore.db.Exec("UPDATE drafts SET status = 'sending' WHERE id = ? AND status = 'pending'", id)
	if n, _ := res.RowsAffected(); err != nil || n != 1 {
		return "", fmt.Errorf("draft %d already handled", id)
	}
	go func() {
		var ok bool
		var detail string
		switch kind {
		case "":
			ok, detail = sendCore(client, recipient, message, media, quoted, true)
		default:
			ok, detail = sendApprovedSpecial(client, recipient, kind, payload)
		}
		status, note := "sent", fmt.Sprintf("🤖 ✅ _נשלח ל%s (אישור אחד = הודעה אחת)._", name)
		if !ok {
			status, note = "failed", fmt.Sprintf("🤖 ⚠️ _השליחה ל%s נכשלה: %s_", name, detail)
		}
		globalStore.db.Exec("UPDATE drafts SET status = ? WHERE id = ?", status, id)
		sendWhatsAppMessage(client, control, note, "")
	}()
	return "sending", nil
}

// registerApprovalAPI: the dashboard's approve / cancel buttons ({"id":12,"approve":true}); same one-shot rules as the reaction.
func registerApprovalAPI(client *whatsmeow.Client) {
	adminHandler("/api/draft_decide", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		id, _ := b["id"].(float64)
		ok, _ := b["approve"].(bool)
		policyMu.Lock()
		control := loadPolicy().ControlGroup
		policyMu.Unlock()
		var cardChat string
		globalStore.db.QueryRow(`SELECT COALESCE(card_chat,'') FROM drafts WHERE id = ?`, int64(id)).Scan(&cardChat)
		if cardChat != "" {
			control = cardChat
		}
		st, err := decideDraft(client, int64(id), ok, control)
		return map[string]any{"status": st}, err
	})
	adminHandler("/api/draft_repost", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		id, _ := b["id"].(float64)
		st, err := repostDraftCard(client, int64(id))
		return map[string]any{"status": st}, err
	})
}

// repostDraftCard (owner 15.11): posts the card of an existing pending draft again (full text + attachment) in its card
// chat, without creating a second draft. The new card becomes the one a ✅ approves.
func repostDraftCard(client *whatsmeow.Client, id int64) (string, error) {
	var recipient, name, message, media, cardChat string
	err := globalStore.db.QueryRow(`SELECT recipient, name, message, COALESCE(media_path,''), COALESCE(card_chat,'')
		FROM drafts WHERE id = ? AND status = 'pending'`, id).Scan(&recipient, &name, &message, &media, &cardChat)
	if err != nil {
		return "", fmt.Errorf("draft %d is not pending", id)
	}
	jid, err := types.ParseJID(recipient)
	if err != nil {
		return "", err
	}
	policyMu.Lock()
	p := loadPolicy()
	policyMu.Unlock()
	card := p.cardChatFor(cardChat)
	ok, res := sendWhatsAppMessage(client, card, draftCardText(id, name, jid, message, media), "")
	if !ok {
		return "", fmt.Errorf("card could not be posted: %s", res)
	}
	if cardID := sentID(res); cardID != "" {
		globalStore.db.Exec("UPDATE drafts SET card_id = ?, card_chat = ? WHERE id = ?", cardID, card, id)
	}
	if media != "" {
		sendWhatsAppMessage(client, card, "🤖 📎 קובץ לטיוטה → "+name, media)
	}
	return "reposted", nil
}

// sendApprovedSpecial sends an approved poll / contact / forward draft as the real message. The approval itself is the
// permission (no shared one-shot state); a pause and the pacer still apply.
func sendApprovedSpecial(client *whatsmeow.Client, recipient, kind, payload string) (bool, string) {
	to, err := types.ParseJID(recipient)
	if err != nil {
		return false, err.Error()
	}
	policyMu.Lock()
	p := loadPolicy()
	policyMu.Unlock()
	if t, e := time.Parse(time.RFC3339, p.PausedUntil); e == nil && time.Now().Before(t) {
		return false, "paused: " + p.PauseReason
	}
	var sp specialPayload
	if err := json.Unmarshal([]byte(payload), &sp); err != nil {
		return false, "bad draft payload"
	}
	msg, desc, err := buildSpecial(client, kind, sp)
	if err != nil {
		return false, err.Error()
	}
	if err := sendPacer.wait(client, to, len(desc)); err != nil {
		recordSent(to, "", desc, "", "", false, err.Error())
		return false, err.Error()
	}
	resp, err := client.SendMessage(context.Background(), to, msg)
	if err != nil {
		recordSent(to, "", desc, "", "", false, err.Error())
		return false, err.Error()
	}
	recordSent(to, resp.ID, desc, "", "", true, kind)
	return true, "sent"
}

// storeApprovalReaction (owner 15.11): someone else's ✅ / 👍 on one of OUR messages in a private chat counts as an answer
// ("מאשר"): the site manager marks ✅ on the daily report instead of writing. It is stored and published as a short
// text message from that person, so the existing reply handling (e.g. the daily-report approval) picks it up unchanged.
func storeApprovalReaction(client *whatsmeow.Client, msg *events.Message, r *waE2E.ReactionMessage) {
	emoji := r.GetText()
	if emoji != "✅" && emoji != "✔️" && emoji != "👍" {
		return
	}
	if !r.GetKey().GetFromMe() || msg.Info.Chat.Server == types.GroupServer {
		return
	}
	chat := normalizeChat(client, msg.Info.Chat)
	chatJID := chat.String()
	sender := normalizeUser(client, msg.Info.Sender.User)
	id := "rx-" + msg.Info.ID
	content := emoji + " (סימון אישור על הודעה)"
	if err := globalStore.StoreMessage(id, chatJID, sender, content, msg.Info.Timestamp, false, "", "", "", nil, nil, nil, 0); err != nil {
		return
	}
	globalStore.db.Exec("UPDATE messages SET quoted_id = ? WHERE id = ? AND chat_jid = ?", r.GetKey().GetID(), id, chatJID)
	publishMessage(id, chatJID, sender, content, "", msg.Info.Timestamp, false)
}

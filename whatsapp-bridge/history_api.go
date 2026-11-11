package main

// On-demand history: ask the owner's phone for older messages of one chat (older than the oldest one we have).
// The phone answers asynchronously with an events.HistorySync of type ON_DEMAND, stored by handleHistorySync, which
// then publishes {"type":"history_sync","chat_jid":…,"count":N} on /api/events.

import (
	"context"
	"database/sql"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

type storedMsg struct {
	ID        string
	Timestamp time.Time
	FromMe    bool
	Sender    string
}

// oldestMessage returns the oldest stored message of a chat (the anchor for an on-demand history request).
func oldestMessage(db *sql.DB, chat string) (storedMsg, error) {
	var m storedMsg
	err := db.QueryRow(`SELECT id, timestamp, coalesce(is_from_me, 0), coalesce(sender, '') FROM messages
		WHERE chat_jid = ? AND coalesce(id, '') <> '' ORDER BY timestamp ASC LIMIT 1`, chat).Scan(&m.ID, &m.Timestamp, &m.FromMe, &m.Sender)
	return m, err
}

// storedMessage returns one stored message of a chat by id (anchor for filling a gap in the middle of a chat).
func storedMessage(db *sql.DB, chat, id string) (storedMsg, error) {
	var m storedMsg
	err := db.QueryRow(`SELECT id, timestamp, coalesce(is_from_me, 0), coalesce(sender, '') FROM messages
		WHERE chat_jid = ? AND id = ?`, chat, id).Scan(&m.ID, &m.Timestamp, &m.FromMe, &m.Sender)
	return m, err
}

// {"jid":"<chat>","count":50} (max 500) → asks the phone for up to count messages older than the oldest stored one.
// With "before_id" the anchor is that stored message instead, so a gap in the middle of a chat can be filled (owner 11.11).
func registerHistoryAPI(client *whatsmeow.Client) {
	adminHandler("/api/history_sync", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, err := types.ParseJID(str(b, "jid"))
		if err != nil || chat.User == "" {
			return nil, errString("need a chat jid")
		}
		count := 50
		if c, ok := b["count"].(float64); ok && c >= 1 {
			count = int(c)
		}
		if count > 500 {
			count = 500
		}
		if globalStore == nil {
			return nil, errString("message store not ready")
		}
		if client == nil || !client.IsConnected() || client.Store.ID == nil {
			return nil, errString("not connected to WhatsApp")
		}
		chat = normalizeChat(client, chat) // LID chats are stored under the phone number
		var old storedMsg
		if before := str(b, "before_id"); before != "" {
			old, err = storedMessage(globalStore.db, chat.String(), before)
			if err == sql.ErrNoRows {
				return nil, errString("before_id is not a stored message of this chat")
			}
		} else {
			old, err = oldestMessage(globalStore.db, chat.String())
		}
		if err == sql.ErrNoRows {
			return nil, errString("no stored messages in this chat yet — nothing to anchor the request on")
		} else if err != nil {
			return nil, err
		}
		info := &types.MessageInfo{
			MessageSource: types.MessageSource{Chat: chat, IsFromMe: old.FromMe},
			ID:            old.ID,
			Timestamp:     old.Timestamp,
		}
		_, err = client.SendMessage(ctx, client.Store.ID.ToNonAD(), client.BuildHistorySyncRequest(info, count),
			whatsmeow.SendRequestExtra{Peer: true})
		if err != nil {
			return nil, err
		}
		return map[string]any{"requested": true, "count": count, "chat_jid": chat.String(),
			"oldest": map[string]any{"id": old.ID, "timestamp": old.Timestamp.Format(time.RFC3339)}}, nil
	})
}

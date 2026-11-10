package main

// Group description history (owner 10.11): some groups take their orders by editing the description (Tzipora's lunch
// group), and WhatsApp keeps no history of it. Every change is stored in group_topics — from the GroupInfo event and
// from a poll of all joined groups every 30 minutes (catches edits made while the bridge was down). Rows older than
// topicKeepDays are dropped, so the table always holds the last ~3 months.

import (
	"context"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

const topicKeepDays = 92

func ensureTopicTable() {
	if globalStore == nil {
		return
	}
	globalStore.db.Exec(`CREATE TABLE IF NOT EXISTS group_topics (chat_jid TEXT, topic TEXT, set_by TEXT, set_at TIMESTAMP,
		seen_at TIMESTAMP)`)
	globalStore.db.Exec(`CREATE INDEX IF NOT EXISTS group_topics_chat ON group_topics (chat_jid, seen_at)`)
}

// storeTopic keeps one row per change: nothing is written when the text equals the newest stored one for the chat.
func storeTopic(client *whatsmeow.Client, chat types.JID, topic string, by types.JID, at time.Time) bool {
	if globalStore == nil || chat.User == "" {
		return false
	}
	var last string
	err := globalStore.db.QueryRow(`SELECT topic FROM group_topics WHERE chat_jid = ? ORDER BY seen_at DESC, rowid DESC LIMIT 1`,
		chat.String()).Scan(&last)
	if err == nil && last == topic {
		return false
	}
	setBy := ""
	if by.User != "" {
		setBy = normalizeUser(client, by.User)
	}
	now := time.Now()
	if at.IsZero() {
		at = now
	}
	globalStore.db.Exec(`INSERT INTO group_topics (chat_jid, topic, set_by, set_at, seen_at) VALUES (?, ?, ?, ?, ?)`,
		chat.String(), topic, setBy, at, now)
	globalStore.db.Exec(`DELETE FROM group_topics WHERE seen_at < ?`, now.AddDate(0, 0, -topicKeepDays))
	return true
}

func pollTopics(client *whatsmeow.Client) {
	if client == nil || !client.IsConnected() {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	groups, err := client.GetJoinedGroups(ctx)
	if err != nil {
		return
	}
	for _, g := range groups {
		if g != nil {
			storeTopic(client, g.JID, g.Topic, g.TopicSetBy, g.TopicSetAt)
		}
	}
}

func registerTopics(client *whatsmeow.Client) {
	ensureTopicTable()
	go func() {
		time.Sleep(2 * time.Minute) // let the connection settle first
		for {
			pollTopics(client)
			time.Sleep(30 * time.Minute)
		}
	}()
}

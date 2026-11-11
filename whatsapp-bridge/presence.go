package main

// Online / typing (owner 11.11): shown next to the chat name in the dashboard, like WhatsApp.
// WhatsApp only sends presence to a client that is itself "available", and while a linked device is available the phone
// gets no notifications (same as an open WhatsApp Web). So the bridge is available only while the dashboard has a chat
// open (/api/presence_watch, refreshed every few seconds) and goes back to unavailable 90 s after the last refresh.

import (
	"context"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

type chatPresence struct {
	Online   bool      `json:"online"`
	LastSeen time.Time `json:"last_seen,omitempty"`
	State    string    `json:"state,omitempty"` // "typing" / "recording" / ""
	By       string    `json:"by,omitempty"`    // who is typing (groups)
	At       time.Time `json:"at"`
}

var (
	presenceMu      sync.Mutex
	presenceByChat  = map[string]*chatPresence{}
	presenceWatched = map[string]time.Time{} // chat -> last subscribe
	presenceLastAsk time.Time
	presenceOnline  bool
)

const presenceIdle = 90 * time.Second

func presenceEntry(chat string) *chatPresence {
	p := presenceByChat[chat]
	if p == nil {
		p = &chatPresence{}
		presenceByChat[chat] = p
	}
	return p
}

// handlePresenceEvent records events.Presence (online / last seen) and events.ChatPresence (typing / recording).
func handlePresenceEvent(client *whatsmeow.Client, evt interface{}) {
	presenceMu.Lock()
	defer presenceMu.Unlock()
	switch v := evt.(type) {
	case *events.Presence:
		chat := normalizeChat(client, v.From.ToNonAD()).String()
		p := presenceEntry(chat)
		p.Online, p.At = !v.Unavailable, time.Now()
		if !v.LastSeen.IsZero() {
			p.LastSeen = v.LastSeen
		}
		if v.Unavailable {
			p.State = ""
		}
	case *events.ChatPresence:
		chat := normalizeChat(client, v.Chat.ToNonAD()).String()
		p := presenceEntry(chat)
		p.State, p.By, p.At = "", "", time.Now()
		if v.State == types.ChatPresenceComposing {
			p.State = "typing"
			if v.Media == types.ChatPresenceMediaAudio {
				p.State = "recording"
			}
			p.By = normalizeChat(client, v.Sender.ToNonAD()).String()
		}
		if v.Chat.Server != types.GroupServer && p.State != "" {
			p.Online = true // typing in a private chat means online
		}
	}
}

// presenceSnapshot: a typing state older than 25 s is stale (WhatsApp sends "paused", but it can be lost).
func presenceSnapshot(chat string) map[string]any {
	presenceMu.Lock()
	defer presenceMu.Unlock()
	p := presenceByChat[chat]
	if p == nil {
		return map[string]any{"known": false, "watching": presenceOnline}
	}
	state := p.State
	if state != "" && time.Since(p.At) > 25*time.Second {
		state = ""
	}
	res := map[string]any{"known": true, "online": p.Online, "state": state, "by": p.By, "at": p.At, "watching": presenceOnline}
	if !p.LastSeen.IsZero() {
		res["last_seen"] = p.LastSeen
	}
	return res
}

func registerPresenceAPI(client *whatsmeow.Client) {
	if client != nil { // nil in the API tests
		client.AddEventHandler(func(evt interface{}) {
			switch evt.(type) {
			case *events.Presence, *events.ChatPresence:
				handlePresenceEvent(client, evt)
			case *events.Connected: // a new connection starts unavailable: the next watch sends available again
				presenceMu.Lock()
				presenceOnline, presenceWatched = false, map[string]time.Time{}
				presenceMu.Unlock()
			}
		})
	}
	// {"jid": chat} — the dashboard has this chat open: be available and subscribe (private chats) at most once a minute.
	adminHandler("/api/presence_watch", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, err := types.ParseJID(str(b, "jid"))
		if err != nil || chat.User == "" {
			return nil, errString("need a chat jid")
		}
		if client == nil || !client.IsConnected() {
			return nil, errString("not connected to WhatsApp")
		}
		chat = normalizeChat(client, chat)
		presenceMu.Lock()
		presenceLastAsk = time.Now()
		goOnline := !presenceOnline
		presenceOnline = true
		last := presenceWatched[chat.String()]
		subscribe := chat.Server != types.GroupServer && time.Since(last) > time.Minute
		if subscribe {
			presenceWatched[chat.String()] = time.Now()
		}
		presenceMu.Unlock()
		if goOnline {
			if err := client.SendPresence(ctx, types.PresenceAvailable); err != nil {
				return nil, err
			}
		}
		if subscribe {
			if err := client.SubscribePresence(ctx, chat); err != nil {
				return nil, err
			}
		}
		return presenceSnapshot(chat.String()), nil
	})
	// {"jid": chat} -> {known, online, state, by, last_seen, watching}
	adminHandler("/api/presence", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, err := types.ParseJID(str(b, "jid"))
		if err != nil || chat.User == "" {
			return nil, errString("need a chat jid")
		}
		if client != nil {
			chat = normalizeChat(client, chat)
		}
		return presenceSnapshot(chat.String()), nil
	})
	go func() { // back to unavailable once the dashboard stopped watching, so the phone gets its notifications again
		for range time.Tick(15 * time.Second) {
			presenceMu.Lock()
			off := presenceOnline && time.Since(presenceLastAsk) > presenceIdle
			if off {
				presenceOnline = false
				presenceWatched = map[string]time.Time{} // subscriptions lapse with it; renew on the next watch
			}
			presenceMu.Unlock()
			if off && client != nil && client.IsConnected() {
				ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
				_ = client.SendPresence(ctx, types.PresenceUnavailable)
				cancel()
			}
		}
	}()
}

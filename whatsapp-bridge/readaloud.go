package main

// Read-aloud grants (owner 09.11 17:25; 11.11: any message, his own too): when the owner reacts 🛞 on a message in any chat, the agent may
// send ONE voice note into that same chat (TTS of that message) without a draft. The grant is created here, from the
// owner's own reaction as WhatsApp delivered it, so nothing else can open the draft gate: one reaction = one voice note
// to that chat, quoting that message, within 10 minutes.

import (
	"strings"
	"sync"
	"time"
)

const readAloudEmoji = "🛞"

var (
	readAloudMu     sync.Mutex
	readAloudGrants = map[string]time.Time{}
)

func readAloudKey(chat, messageID string) string { return chat + "|" + messageID }

// grantReadAloud records the owner's 🛞 reaction on messageID in chat (chat as published in the reaction event).
func grantReadAloud(chat, messageID string) {
	readAloudMu.Lock()
	defer readAloudMu.Unlock()
	now := time.Now()
	for k, t := range readAloudGrants {
		if now.Sub(t) > 10*time.Minute {
			delete(readAloudGrants, k)
		}
	}
	readAloudGrants[readAloudKey(chat, messageID)] = now
}

// consumeReadAloud: true once per grant, only for a voice-note file without text, sent to the chat the owner reacted in.
func consumeReadAloud(recipient, messageID, message, mediaPath string) bool {
	if messageID == "" || message != "" || !strings.HasSuffix(strings.ToLower(mediaPath), ".ogg") {
		return false
	}
	readAloudMu.Lock()
	defer readAloudMu.Unlock()
	k := readAloudKey(recipient, messageID)
	t, ok := readAloudGrants[k]
	if !ok || time.Since(t) > 10*time.Minute {
		return false
	}
	delete(readAloudGrants, k)
	return true
}

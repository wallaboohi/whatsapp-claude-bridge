package main

// Quoted replies.

import (
	"strings"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

// quoteContext builds the ContextInfo that makes a text message a reply to quotedID in the same chat.
func quoteContext(chat types.JID, quotedID string) *waProto.ContextInfo {
	if quotedID == "" || globalStore == nil {
		return nil
	}
	var sender, content string
	var fromMe bool
	err := globalStore.db.QueryRow("SELECT sender, content, is_from_me FROM messages WHERE id = ? AND chat_jid = ?",
		quotedID, chat.String()).Scan(&sender, &content, &fromMe)
	if err != nil {
		return nil
	}
	participant := chat
	if (sender == "me" || sender == "") && globalClient != nil && globalClient.Store.ID != nil {
		participant = globalClient.Store.ID.ToNonAD() // the bridge stores its own sends as "me" (owner 20.11 12:17)
	} else if chat.Server == types.GroupServer || fromMe {
		server := types.DefaultUserServer
		if !strings.HasPrefix(sender, "972") && len(sender) >= 14 {
			server = types.HiddenUserServer // LID-addressed participant
		}
		participant = types.NewJID(sender, server)
	}
	return &waProto.ContextInfo{
		StanzaID:      proto.String(quotedID),
		Participant:   proto.String(participant.String()),
		QuotedMessage: &waProto.Message{Conversation: proto.String(content)},
	}
}

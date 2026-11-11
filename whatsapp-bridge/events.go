package main

// WhatsApp event handling: incoming messages, chat names.

import (
	"context"
	"fmt"
	"os"
	"reflect"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"google.golang.org/protobuf/reflect/protoreflect"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

// registerEventHandlers wires WhatsApp events: messages, history sync, media re-upload answers, group renames,
// bans, connection state, logout / stream replaced.
func registerEventHandlers(client *whatsmeow.Client, messageStore *MessageStore, logger waLog.Logger) {
	client.AddEventHandler(func(evt interface{}) {
		switch v := evt.(type) {
		case *events.Message:
			if handleReaction(client, v) {
				break
			}
			// Process regular messages
			handleMessage(client, messageStore, v, logger)

		case *events.HistorySync:
			// Process history sync events
			handleHistorySync(client, messageStore, v, logger)

		case *events.MediaRetry:
			// Hand the phone's re-upload answer to whoever is waiting for it
			if ch, ok := mediaRetryWaiters.Load(v.MessageID); ok {
				ch.(chan *events.MediaRetry) <- v
			}

		case *events.GroupInfo:
			if v.Topic != nil {
				storeTopic(client, v.JID, v.Topic.Topic, v.Topic.TopicSetBy, v.Topic.TopicSetAt)
			}
			// Group renamed: keep the stored chat name in step with WhatsApp (GetChatName never overwrites an existing name).
			if v.Name != nil && v.Name.Name != "" {
				if _, err := messageStore.db.Exec("UPDATE chats SET name = ? WHERE jid = ?", v.Name.Name, v.JID.String()); err != nil {
					logger.Warnf("Could not store new group name for %s: %v", v.JID.String(), err)
				} else {
					logger.Infof("Group %s renamed to: %s", v.JID.String(), v.Name.Name)
				}
			}

		case *events.TemporaryBan:
			pauseSends(client, 24*time.Hour, "WhatsApp הודיע על חסימה זמנית: "+v.String())

		case *events.Connected:
			markConnected()
			logger.Infof("Connected to WhatsApp")

		case *events.Disconnected:
			markDisconnected()

		case *events.LoggedOut:
			// the session is gone (removed from "Linked devices"): exit so the run-bridge loop restarts into the QR flow
			logger.Warnf("Device logged out, please scan QR code to log in again — restarting")
			logConn("logged_out")
			go func() { time.Sleep(3 * time.Second); os.Exit(1) }()

		case *events.StreamReplaced:
			// another client took over this session; whatsmeow does not reconnect by itself. Wait (to avoid a
			// tug-of-war with a second copy) then exit so the loop starts a fresh connection.
			logger.Warnf("Stream replaced by another client — restarting in 2 minutes")
			logConn("stream_replaced")
			go func() { time.Sleep(2 * time.Minute); os.Exit(1) }()
		}
	})
}

// Handle regular incoming messages with media support
func handleMessage(client *whatsmeow.Client, messageStore *MessageStore, msg *events.Message, logger waLog.Logger) {
	if handleProtocol(client, messageStore, msg) {
		return
	}
	if handlePolls(client, messageStore, msg, logger) {
		return
	}

	// Save message to database (LID-addressed private chats are stored under the phone number)
	chat := normalizeChat(client, msg.Info.Chat)
	chatJID := chat.String()
	sender := normalizeUser(client, msg.Info.Sender.User)

	// Get appropriate chat name (pass nil for conversation since we don't have one for regular messages)
	name := GetChatName(client, messageStore, chat, chatJID, nil, sender, logger)

	// Update chat in database with the message timestamp (keeps last message time updated)
	err := messageStore.StoreChat(chatJID, name, msg.Info.Timestamp)
	if err != nil {
		logger.Warnf("Failed to store chat: %v", err)
	}

	// Extract text content
	content := extractTextContent(msg.Message)
	if content == "" {
		content = eventResponseText(client, messageStore, msg)
	}

	// Extract media info
	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength := extractMediaInfo(msg.Message)
	if mediaType != "" {
		filename = mediaFilename(msg.Info.ID, mediaType, filename)
	}

	// Skip if there's no content and no media — but say which message type was dropped (owner 11.11: a weekly poll
	// vanished without a trace; this log is how an unknown type is found next time)
	if content == "" && mediaType == "" {
		var kinds []string
		msg.Message.ProtoReflect().Range(func(fd protoreflect.FieldDescriptor, _ protoreflect.Value) bool {
			kinds = append(kinds, string(fd.Name()))
			return true
		})
		if len(kinds) > 0 && !(len(kinds) == 1 && kinds[0] == "messageContextInfo") {
			logger.Infof("Dropped message %s in %s (no text/media): %s", msg.Info.ID, chatJID, strings.Join(kinds, ","))
		}
		return
	}

	// Store message in database
	err = messageStore.StoreMessage(
		msg.Info.ID,
		chatJID,
		sender,
		content,
		msg.Info.Timestamp,
		msg.Info.IsFromMe,
		mediaType,
		filename,
		url,
		mediaKey,
		fileSHA256,
		fileEncSHA256,
		fileLength,
	)

	if err != nil {
		logger.Warnf("Failed to store message: %v", err)
	} else {
		// which message this one replied to (the agent uses it for "מאשר לך שליחה" as a reply on a draft card);
		// stored before the event goes out so a listener can read it
		if q := quotedStanza(msg.Message); q != "" {
			messageStore.db.Exec("UPDATE messages SET quoted_id = ? WHERE id = ? AND chat_jid = ?", q, msg.Info.ID, chatJID)
		}
		publishMessage(msg.Info.ID, chatJID, sender, content, mediaType, msg.Info.Timestamp, msg.Info.IsFromMe)
		if mediaType != "" {
			autoDownload(client, messageStore, msg.Info.ID, chatJID)
		}
		// Log message reception
		timestamp := msg.Info.Timestamp.Format("2006-01-02 15:04:05")
		direction := "←"
		if msg.Info.IsFromMe {
			direction = "→"
		}

		// Log based on message type
		if mediaType != "" {
			fmt.Printf("[%s] %s %s: [%s: %s] %s\n", timestamp, direction, sender, mediaType, filename, content)
		} else if content != "" {
			fmt.Printf("[%s] %s %s: %s\n", timestamp, direction, sender, content)
		}
	}
}

// GetChatName determines the appropriate name for a chat based on JID and other info
func GetChatName(client *whatsmeow.Client, messageStore *MessageStore, jid types.JID, chatJID string, conversation interface{}, sender string, logger waLog.Logger) string {
	// First, check if chat already exists in database with a name
	var existingName string
	err := messageStore.db.QueryRow("SELECT name FROM chats WHERE jid = ?", chatJID).Scan(&existingName)
	if err == nil && existingName != "" {
		// Chat exists with a name, use that
		logger.Infof("Using existing chat name for %s: %s", chatJID, existingName)
		return existingName
	}

	// Need to determine chat name
	var name string

	if jid.Server == "g.us" {
		// This is a group chat
		logger.Infof("Getting name for group: %s", chatJID)

		// Use conversation data if provided (from history sync)
		if conversation != nil {
			// Extract name from conversation if available
			// This uses type assertions to handle different possible types
			var displayName, convName *string
			// Try to extract the fields we care about regardless of the exact type
			v := reflect.ValueOf(conversation)
			if v.Kind() == reflect.Ptr && !v.IsNil() {
				v = v.Elem()

				// Try to find DisplayName field
				if displayNameField := v.FieldByName("DisplayName"); displayNameField.IsValid() && displayNameField.Kind() == reflect.Ptr && !displayNameField.IsNil() {
					dn := displayNameField.Elem().String()
					displayName = &dn
				}

				// Try to find Name field
				if nameField := v.FieldByName("Name"); nameField.IsValid() && nameField.Kind() == reflect.Ptr && !nameField.IsNil() {
					n := nameField.Elem().String()
					convName = &n
				}
			}

			// Use the name we found
			if displayName != nil && *displayName != "" {
				name = *displayName
			} else if convName != nil && *convName != "" {
				name = *convName
			}
		}

		// If we didn't get a name, try group info
		if name == "" {
			groupInfo, err := client.GetGroupInfo(context.Background(), jid)
			if err == nil && groupInfo.Name != "" {
				name = groupInfo.Name
			} else {
				// Fallback name for groups
				name = fmt.Sprintf("Group %s", jid.User)
			}
		}

		logger.Infof("Using group name: %s", name)
	} else {
		// This is an individual contact
		logger.Infof("Getting name for contact: %s", chatJID)

		// Just use contact info (full name)
		contact, err := client.Store.Contacts.GetContact(context.Background(), jid)
		if err == nil && contact.FullName != "" {
			name = contact.FullName
		} else if sender != "" {
			// Fallback to sender
			name = sender
		} else {
			// Last fallback to JID
			name = jid.User
		}

		logger.Infof("Using contact name: %s", name)
	}

	return name
}

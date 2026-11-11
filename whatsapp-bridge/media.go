package main

// Media: extraction from incoming messages, download / decrypt, re-upload requests.

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/proto/waMmsRetry"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

// Extract media info from a message
func extractMediaInfo(msg *waProto.Message) (mediaType string, filename string, url string, mediaKey []byte, fileSHA256 []byte, fileEncSHA256 []byte, fileLength uint64) {
	if msg == nil {
		return "", "", "", nil, nil, nil, 0
	}

	// Check for image message
	if img := msg.GetImageMessage(); img != nil {
		return "image", "image_" + time.Now().Format("20060102_150405") + ".jpg",
			img.GetURL(), img.GetMediaKey(), img.GetFileSHA256(), img.GetFileEncSHA256(), img.GetFileLength()
	}

	// Check for video message
	if vid := msg.GetVideoMessage(); vid != nil {
		return "video", "video_" + time.Now().Format("20060102_150405") + ".mp4",
			vid.GetURL(), vid.GetMediaKey(), vid.GetFileSHA256(), vid.GetFileEncSHA256(), vid.GetFileLength()
	}

	// Check for audio message
	if aud := msg.GetAudioMessage(); aud != nil {
		return "audio", "audio_" + time.Now().Format("20060102_150405") + ".ogg",
			aud.GetURL(), aud.GetMediaKey(), aud.GetFileSHA256(), aud.GetFileEncSHA256(), aud.GetFileLength()
	}

	// Check for document message
	if doc := msg.GetDocumentMessage(); doc != nil {
		filename := doc.GetFileName()
		if filename == "" {
			filename = "document_" + time.Now().Format("20060102_150405")
		}
		return "document", filename,
			doc.GetURL(), doc.GetMediaKey(), doc.GetFileSHA256(), doc.GetFileEncSHA256(), doc.GetFileLength()
	}

	return "", "", "", nil, nil, nil, 0
}

// DownloadMediaRequest represents the request body for the download media API
type DownloadMediaRequest struct {
	MessageID string `json:"message_id"`
	ChatJID   string `json:"chat_jid"`
}

// DownloadMediaResponse represents the response for the download media API
type DownloadMediaResponse struct {
	Success  bool   `json:"success"`
	Message  string `json:"message"`
	Filename string `json:"filename,omitempty"`
	Path     string `json:"path,omitempty"`
}

// MediaDownloader implements the whatsmeow.DownloadableMessage interface
type MediaDownloader struct {
	URL           string
	DirectPath    string
	MediaKey      []byte
	FileLength    uint64
	FileSHA256    []byte
	FileEncSHA256 []byte
	MediaType     whatsmeow.MediaType
}

// GetDirectPath implements the DownloadableMessage interface
func (d *MediaDownloader) GetDirectPath() string {
	return d.DirectPath
}

// GetURL implements the DownloadableMessage interface
func (d *MediaDownloader) GetURL() string {
	return d.URL
}

// GetMediaKey implements the DownloadableMessage interface
func (d *MediaDownloader) GetMediaKey() []byte {
	return d.MediaKey
}

// GetFileLength implements the DownloadableMessage interface
func (d *MediaDownloader) GetFileLength() uint64 {
	return d.FileLength
}

// GetFileSHA256 implements the DownloadableMessage interface
func (d *MediaDownloader) GetFileSHA256() []byte {
	return d.FileSHA256
}

// GetFileEncSHA256 implements the DownloadableMessage interface
func (d *MediaDownloader) GetFileEncSHA256() []byte {
	return d.FileEncSHA256
}

// GetMediaType implements the DownloadableMessage interface
func (d *MediaDownloader) GetMediaType() whatsmeow.MediaType {
	return d.MediaType
}

// Function to download media from a message
func downloadMedia(client *whatsmeow.Client, messageStore *MessageStore, messageID, chatJID string) (bool, string, string, string, error) {
	// Query the database for the message
	var mediaType, filename, url string
	var mediaKey, fileSHA256, fileEncSHA256 []byte
	var fileLength uint64
	var err error

	// First, check if we already have this file
	chatDir := fmt.Sprintf("store/%s", strings.ReplaceAll(chatJID, ":", "_"))
	localPath := ""

	// Get media info from the database
	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength, err = messageStore.GetMediaInfo(messageID, chatJID)

	if err != nil {
		// Try to get basic info if extended info isn't available
		err = messageStore.db.QueryRow(
			"SELECT media_type, filename FROM messages WHERE id = ? AND chat_jid = ?",
			messageID, chatJID,
		).Scan(&mediaType, &filename)

		if err != nil {
			return false, "", "", "", fmt.Errorf("failed to find message: %v", err)
		}
	}

	// Check if this is a media message
	if mediaType == "" {
		return false, "", "", "", fmt.Errorf("not a media message")
	}

	// Create directory for the chat if it doesn't exist
	if err := os.MkdirAll(chatDir, 0755); err != nil {
		return false, "", "", "", fmt.Errorf("failed to create chat directory: %v", err)
	}

	// Generate a local path for the file
	localPath = fmt.Sprintf("%s/%s", chatDir, filename)

	// Get absolute path
	absPath, err := filepath.Abs(localPath)
	if err != nil {
		return false, "", "", "", fmt.Errorf("failed to get absolute path: %v", err)
	}

	// Check if file already exists
	if _, err := os.Stat(localPath); err == nil {
		// File exists, return it
		return true, mediaType, filename, absPath, nil
	}

	// If we don't have all the media info we need, we can't download
	if url == "" || len(mediaKey) == 0 || len(fileSHA256) == 0 || len(fileEncSHA256) == 0 || fileLength == 0 {
		return false, "", "", "", fmt.Errorf("incomplete media information for download")
	}

	fmt.Printf("Attempting to download media for message %s in chat %s...\n", messageID, chatJID)

	// Extract direct path from URL
	directPath := extractDirectPathFromURL(url)

	// Create a downloader that implements DownloadableMessage
	var waMediaType whatsmeow.MediaType
	switch mediaType {
	case "image":
		waMediaType = whatsmeow.MediaImage
	case "video":
		waMediaType = whatsmeow.MediaVideo
	case "audio":
		waMediaType = whatsmeow.MediaAudio
	case "document":
		waMediaType = whatsmeow.MediaDocument
	default:
		return false, "", "", "", fmt.Errorf("unsupported media type: %s", mediaType)
	}

	downloader := &MediaDownloader{
		URL:           url,
		DirectPath:    directPath,
		MediaKey:      mediaKey,
		FileLength:    fileLength,
		FileSHA256:    fileSHA256,
		FileEncSHA256: fileEncSHA256,
		MediaType:     waMediaType,
	}

	// Download the media using whatsmeow client
	mediaData, err := client.Download(context.Background(), downloader)
	if err != nil && (errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith403) || errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith404) ||
		errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith410) || errors.Is(err, whatsmeow.ErrInvalidMediaHMAC) ||
		errors.Is(err, whatsmeow.ErrInvalidMediaEncSHA256) ||
		// whatsmeow wraps the last host's error with %v, so errors.Is does not see it
		strings.Contains(err.Error(), whatsmeow.ErrInvalidMediaHMAC.Error()) ||
		strings.Contains(err.Error(), whatsmeow.ErrInvalidMediaEncSHA256.Error())) {
		// Media expired on the server, or (old / history-synced media, owner 10.11) the CDN no longer serves the
		// original blob: ask the phone — which still has every file — to re-upload it, then retry with the new path
		fmt.Printf("Media not downloadable (%v), requesting re-upload from phone for %s...\n", err, messageID)
		// diagnostics for the invalid-hmac bug (owner 11.11): key fingerprint, stored type and length, hash sizes
		keyFp := sha256.Sum256(mediaKey)
		fmt.Printf("Media diag %s: type=%s key=%d bytes fp=%x file_length=%d sha256=%d enc_sha256=%d\n", messageID, mediaType, len(mediaKey), keyFp[:4], fileLength, len(fileSHA256), len(fileEncSHA256))
		var newPath string
		newPath, err = requestMediaReupload(client, messageStore, messageID, chatJID, mediaKey)
		if err == nil {
			// the phone re-uploads the same encrypted blob under a new path, so the stored hashes still apply
			// (whatsmeow's documented MediaRetry flow); fall back to the new file's own hash only if that fails (owner 11.11)
			mediaData, err = client.DownloadMediaWithPath(context.Background(), newPath, fileEncSHA256, fileSHA256, mediaKey, waMediaType, "", false)
			if err != nil {
				fmt.Printf("Re-uploaded media with the stored hashes failed (%v), trying the new file's hash...\n", err)
				mediaData, err = downloadReuploadedMedia(client, newPath, mediaKey, waMediaType, mediaType)
			}
			if s, ok := lastRetrySecret.LoadAndDelete(messageID); ok && err != nil && len(s.([]byte)) == 32 {
				fmt.Printf("Trying the re-upload's message secret as the media key...\n")
				mediaData, err = downloadReuploadedMedia(client, newPath, s.([]byte), waMediaType, mediaType)
			}
		}
	}
	if err != nil {
		return false, "", "", "", fmt.Errorf("failed to download media: %v", err)
	}

	// Save the downloaded media to file
	if err := os.WriteFile(localPath, mediaData, 0644); err != nil {
		return false, "", "", "", fmt.Errorf("failed to save media file: %v", err)
	}

	fmt.Printf("Successfully downloaded %s media to %s (%d bytes)\n", mediaType, absPath, len(mediaData))
	return true, mediaType, filename, absPath, nil
}

// downloadReuploadedMedia downloads media the phone re-uploaded. The re-upload is re-encrypted, so the
// stored hashes no longer match; the ciphertext hash is taken from the new file and authenticity is
// still verified by whatsmeow's HMAC check with the original media key.
func downloadReuploadedMedia(client *whatsmeow.Client, directPath string, mediaKey []byte, waMediaType whatsmeow.MediaType, mmsType string) ([]byte, error) {
	resp, err := http.Get("https://mmg.whatsapp.net" + directPath)
	if err != nil {
		return nil, fmt.Errorf("failed to fetch re-uploaded media: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("re-uploaded media fetch failed with status %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read re-uploaded media: %v", err)
	}
	encHash := sha256.Sum256(raw)
	fmt.Printf("Re-uploaded file: %d bytes, enc sha256 %x\n", len(raw), encHash[:4])
	return client.DownloadMediaWithPath(context.Background(), directPath, encHash[:], nil, mediaKey, waMediaType, mmsType, true)
}

// mediaRetryWaiters maps a message ID to the channel waiting for its MediaRetry event
var mediaRetryWaiters sync.Map

// lastRetrySecret keeps the message secret the phone sent with a re-upload (experiment, owner 11.11)
var lastRetrySecret sync.Map

// requestMediaReupload asks the phone to re-upload expired media and returns the new direct path
func requestMediaReupload(client *whatsmeow.Client, messageStore *MessageStore, messageID, chatJID string, mediaKey []byte) (string, error) {
	var isFromMe bool
	var stored string
	if err := messageStore.db.QueryRow(
		"SELECT is_from_me, coalesce(sender, '') FROM messages WHERE id = ? AND chat_jid = ?",
		messageID, chatJID,
	).Scan(&isFromMe, &stored); err != nil {
		return "", fmt.Errorf("failed to look up message: %v", err)
	}

	chat, err := types.ParseJID(chatJID)
	if err != nil {
		return "", fmt.Errorf("invalid chat JID: %v", err)
	}
	sender := chat
	if isFromMe && client.Store.ID != nil {
		sender = client.Store.ID.ToNonAD()
	} else if chat.Server == types.GroupServer && stored != "" && stored != chat.User {
		// in a group the retry names the author (rows synced before 10.11 may only hold the group id)
		server := types.DefaultUserServer
		if !strings.HasPrefix(stored, "972") && len(stored) >= 14 {
			server = types.HiddenUserServer
		}
		sender = types.NewJID(stored, server)
	}
	info := &types.MessageInfo{
		MessageSource: types.MessageSource{Chat: chat, Sender: sender, IsFromMe: isFromMe},
		ID:            messageID,
	}

	ch := make(chan *events.MediaRetry, 1)
	mediaRetryWaiters.Store(messageID, ch)
	defer mediaRetryWaiters.Delete(messageID)

	if err := client.SendMediaRetryReceipt(context.Background(), info, mediaKey); err != nil {
		return "", fmt.Errorf("failed to send media retry receipt: %v", err)
	}

	select {
	case evt := <-ch:
		retryData, err := whatsmeow.DecryptMediaRetryNotification(evt, mediaKey)
		if err != nil {
			return "", fmt.Errorf("failed to decrypt media retry notification: %v", err)
		}
		if retryData.GetResult() != waMmsRetry.MediaRetryNotification_SUCCESS {
			return "", fmt.Errorf("phone could not re-upload media: %s", retryData.GetResult())
		}
		if s := retryData.GetMessageSecret(); len(s) > 0 {
			fmt.Printf("Re-upload came with a message secret (%d bytes)\n", len(s))
			lastRetrySecret.Store(messageID, s)
		}
		return retryData.GetDirectPath(), nil
	case <-time.After(45 * time.Second):
		return "", fmt.Errorf("timed out waiting for phone to re-upload media")
	}
}

// Extract direct path from a WhatsApp media URL
func extractDirectPathFromURL(url string) string {
	// The direct path is typically in the URL, we need to extract it
	// Example URL: https://mmg.whatsapp.net/v/t62.7118-24/13812002_698058036224062_3424455886509161511_n.enc?ccb=11-4&oh=...

	// Find the path part after the domain
	parts := strings.SplitN(url, ".net/", 2)
	if len(parts) < 2 {
		return url // Return original URL if parsing fails
	}

	pathPart := parts[1]

	// Keep the query string: its oh/oe tokens authorize the download, without them the CDN answers 403

	// Create proper direct path format
	return "/" + pathPart
}

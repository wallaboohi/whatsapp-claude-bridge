// phone-ack: a second, independent WhatsApp linked device that lives in Termux on the owner's phone (owner 20.11.2026).
// It never answers and never sends a message. When the owner writes in the control group and no computer of the bridge
// reacted within wait_sec, it reacts with the "seen" emoji itself, keeps the message in queue.jsonl and raises an
// Android notification. The computers' own 👀 cancels it, so it only acts when the whole chain is unavailable.
//
//	phone-ack -dir ~/phone-ack -pair 9725XXXXXXXX     # one time: prints a code to type in WhatsApp > Linked devices
//	phone-ack -dir ~/phone-ack                        # run (config.json: {"groups": ["...@g.us"]})
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "modernc.org/sqlite"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

type config struct {
	Groups    []string `json:"groups"`
	WaitSec   int      `json:"wait_sec"`
	MaxAgeSec int      `json:"max_age_sec"`
	Emoji     string   `json:"emoji"`
}

type ack struct {
	mu      sync.Mutex
	cfg     config
	groups  map[string]bool // group user parts
	client  *whatsmeow.Client
	dir     string
	seen    map[string]bool // message ids a computer already reacted to
	pending map[string]bool
}

func main() {
	home, _ := os.UserHomeDir()
	dir := flag.String("dir", filepath.Join(home, "phone-ack"), "state directory")
	pair := flag.String("pair", "", "phone number (international, digits) to link with a pairing code")
	flag.Parse()
	// Android has no /etc/resolv.conf: resolve through public DNS
	net.DefaultResolver = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, n, a string) (net.Conn, error) {
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "udp", "8.8.8.8:53")
	}}
	_ = os.MkdirAll(*dir, 0o700)
	logger := waLog.Stdout("phone-ack", "INFO", true)
	db, err := sql.Open("sqlite", "file:"+filepath.Join(*dir, "store.db")+"?_pragma=foreign_keys(1)&_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)")
	if err != nil {
		logger.Errorf("db: %v", err)
		os.Exit(1)
	}
	container := sqlstore.NewWithDB(db, "sqlite3", waLog.Stdout("db", "WARN", true))
	if err := container.Upgrade(context.Background()); err != nil {
		logger.Errorf("db upgrade: %v", err)
		os.Exit(1)
	}
	dev, err := container.GetFirstDevice(context.Background())
	if err != nil {
		logger.Errorf("device: %v", err)
		os.Exit(1)
	}
	client := whatsmeow.NewClient(dev, logger)
	a := &ack{client: client, dir: *dir, seen: map[string]bool{}, pending: map[string]bool{}}
	a.cfg = config{WaitSec: 10, MaxAgeSec: 120, Emoji: "👀"}
	if b, err := os.ReadFile(filepath.Join(*dir, "config.json")); err == nil {
		_ = json.Unmarshal(b, &a.cfg)
	}
	if a.cfg.WaitSec <= 0 {
		a.cfg.WaitSec = 10
	}
	if a.cfg.MaxAgeSec <= 0 {
		a.cfg.MaxAgeSec = 120
	}
	if a.cfg.Emoji == "" {
		a.cfg.Emoji = "👀"
	}
	a.groups = map[string]bool{}
	for _, g := range a.cfg.Groups {
		if j, err := types.ParseJID(g); err == nil {
			a.groups[j.User] = true
		}
	}
	paired := make(chan struct{}, 1)
	client.AddEventHandler(func(evt interface{}) {
		switch v := evt.(type) {
		case *events.PairSuccess:
			logger.Infof("paired as %s", v.ID)
			select {
			case paired <- struct{}{}:
			default:
			}
		case *events.LoggedOut:
			a.notify("עזר: ווצאפ ניתק את המכשיר", "צריך לקשר מחדש את עזר הטלפון")
		case *events.Message:
			a.onMessage(v)
		}
	})
	if client.Store.ID == nil {
		if *pair == "" {
			logger.Errorf("not linked: run once with -pair <phone number>")
			os.Exit(2)
		}
		if err := client.Connect(); err != nil {
			logger.Errorf("connect: %v", err)
			os.Exit(1)
		}
		code, err := client.PairPhone(context.Background(), *pair, true, whatsmeow.PairClientChrome, "Chrome (Linux)")
		if err != nil {
			logger.Errorf("pair: %v", err)
			os.Exit(1)
		}
		fmt.Println("PAIRING CODE:", code)
		fmt.Println("WhatsApp > Settings > Linked devices > Link a device > Link with phone number instead, and type the code.")
		select {
		case <-paired:
		case <-time.After(5 * time.Minute):
			logger.Errorf("pairing timed out")
			os.Exit(1)
		}
		time.Sleep(3 * time.Second)
	} else if err := client.Connect(); err != nil {
		logger.Errorf("connect: %v", err)
		os.Exit(1)
	}
	logger.Infof("running; %d groups, wait=%ds", len(a.groups), a.cfg.WaitSec)
	go func() {
		for {
			a.writeStatus()
			time.Sleep(30 * time.Second)
		}
	}()
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGINT, syscall.SIGTERM)
	<-ch
	client.Disconnect()
}

func (a *ack) writeStatus() {
	b, _ := json.Marshal(map[string]interface{}{"at": time.Now().Format(time.RFC3339), "connected": a.client.IsConnected(), "groups": len(a.groups)})
	_ = os.WriteFile(filepath.Join(a.dir, "status.json"), b, 0o600)
}

func (a *ack) notify(title, text string) {
	_ = exec.Command("termux-notification", "--id", "9101", "--title", title, "--content", text).Run()
}

func messageText(m *waE2E.Message) string {
	if m == nil {
		return ""
	}
	if t := m.GetConversation(); t != "" {
		return t
	}
	if e := m.GetExtendedTextMessage(); e != nil {
		return e.GetText()
	}
	switch {
	case m.GetAudioMessage() != nil:
		return "[הקלטה]"
	case m.GetImageMessage() != nil:
		return "[תמונה] " + m.GetImageMessage().GetCaption()
	case m.GetDocumentMessage() != nil:
		return "[קובץ]"
	case m.GetVideoMessage() != nil:
		return "[וידאו]"
	}
	return ""
}

func (a *ack) onMessage(v *events.Message) {
	if !a.groups[v.Info.Chat.User] || !v.Info.IsFromMe {
		return
	}
	if r := v.Message.GetReactionMessage(); r != nil { // a computer reacted to a message: nothing for the phone to do
		a.mu.Lock()
		a.seen[r.GetKey().GetID()] = true
		a.mu.Unlock()
		return
	}
	if v.Message.GetProtocolMessage() != nil || v.IsEdit {
		return
	}
	text := messageText(v.Message)
	if text == "" || strings.HasPrefix(text, "🤖") || strings.HasPrefix(strings.TrimSpace(text), "_") {
		return // the bridge's own messages
	}
	if time.Since(v.Info.Timestamp) > time.Duration(a.cfg.MaxAgeSec)*time.Second {
		return // old backlog after a reconnect
	}
	id := v.Info.ID
	a.mu.Lock()
	if a.pending[id] {
		a.mu.Unlock()
		return
	}
	a.pending[id] = true
	a.mu.Unlock()
	time.AfterFunc(time.Duration(a.cfg.WaitSec)*time.Second, func() { a.check(v, id, text) })
}

func (a *ack) check(v *events.Message, id, text string) {
	a.mu.Lock()
	reacted := a.seen[id]
	delete(a.pending, id)
	a.mu.Unlock()
	if reacted {
		return
	}
	_, err := a.client.SendMessage(context.Background(), v.Info.Chat, a.client.BuildReaction(v.Info.Chat, v.Info.Sender, id, a.cfg.Emoji))
	rec, _ := json.Marshal(map[string]interface{}{"at": time.Now().Format(time.RFC3339), "id": id, "text": truncate(text, 300), "reacted": err == nil, "error": fmt.Sprint(err)})
	if f, e := os.OpenFile(filepath.Join(a.dir, "queue.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600); e == nil {
		_, _ = f.Write(append(rec, '\n'))
		_ = f.Close()
	}
	a.notify("הגשר לא זמין", "ההודעה נשמרה ותטופל כשיחזור: "+truncate(text, 60))
}

func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}

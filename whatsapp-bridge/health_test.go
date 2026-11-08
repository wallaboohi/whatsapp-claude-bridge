package main

import (
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

func TestHealthStateAndReceipts(t *testing.T) {
	prev := globalStore
	defer func() { globalStore = prev }()
	globalStore = memStore(t)
	globalStore.db.Exec("CREATE TABLE IF NOT EXISTS conn_log (at TIMESTAMP, event TEXT)")
	ensureHealthTables()

	setHealth("outdated", "405", "waiting for an update")
	if h := healthSnapshot(); h.Problem != "outdated" || h.Since == "" {
		t.Fatalf("health not set: %+v", h)
	}
	clearHealth("connected")
	if h := healthSnapshot(); h.Problem != "" {
		t.Fatalf("health not cleared: %+v", h)
	}

	globalStore.db.Exec("INSERT INTO send_log (recipient, message_id, ok, detail, timestamp) VALUES ('x', 'M1', 1, '', '2026-11-08')")
	rcpt := func(id string, typ types.ReceiptType) *events.Receipt {
		r := &events.Receipt{MessageIDs: []types.MessageID{id}, Type: typ, Timestamp: time.Now()}
		r.Sender = types.NewJID("972500000001", types.DefaultUserServer)
		r.Chat = r.Sender
		return r
	}
	storeReceipt(rcpt("M1", types.ReceiptTypeRead))
	storeReceipt(rcpt("M1", types.ReceiptTypeRead))    // duplicate
	storeReceipt(rcpt("OTHER", types.ReceiptTypeRead)) // not ours
	storeReceipt(rcpt("M1", types.ReceiptTypeRetry))   // not a delivery state
	var n int
	globalStore.db.QueryRow("SELECT COUNT(*) FROM receipts").Scan(&n)
	if n != 1 {
		t.Fatalf("want 1 receipt, got %d", n)
	}

	storeCall(types.NewJID("972500000002", types.DefaultUserServer), "C1", time.Now(), "offer", "")
	storeCall(types.NewJID("972500000002", types.DefaultUserServer), "C1", time.Now(), "terminate", "timeout")
	globalStore.db.QueryRow("SELECT COUNT(*) FROM calls WHERE call_id = 'C1'").Scan(&n)
	if n != 2 {
		t.Fatalf("want 2 call events, got %d", n)
	}
}

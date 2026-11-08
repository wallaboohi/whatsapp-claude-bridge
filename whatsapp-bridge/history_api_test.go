package main

import (
	"database/sql"
	"testing"
	"time"
)

// The on-demand request is anchored on the chat's OLDEST stored message (not another chat's, not an empty id).
func TestOldestMessageAnchor(t *testing.T) {
	s := memStore(t)
	t0 := time.Date(2026, 11, 1, 9, 0, 0, 0, time.UTC)
	s.StoreMessage("new", "c@g.us", "u1", "later", t0.Add(2*time.Hour), false, "", "", "", nil, nil, nil, 0)
	s.StoreMessage("old", "c@g.us", "me", "first", t0, true, "", "", "", nil, nil, nil, 0)
	s.StoreMessage("other", "d@g.us", "u2", "elsewhere", t0.Add(-24*time.Hour), false, "", "", "", nil, nil, nil, 0)
	m, err := oldestMessage(s.db, "c@g.us")
	if err != nil {
		t.Fatal(err)
	}
	if m.ID != "old" || !m.FromMe || !m.Timestamp.Equal(t0) {
		t.Fatalf("wrong anchor: %+v", m)
	}
	if _, err := oldestMessage(s.db, "empty@g.us"); err != sql.ErrNoRows {
		t.Fatalf("a chat with no messages must report sql.ErrNoRows, got %v", err)
	}
}

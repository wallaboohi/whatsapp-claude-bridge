package main

import (
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types"
)

func TestGroupTopicHistory(t *testing.T) {
	prev := globalStore
	defer func() { globalStore = prev }()
	globalStore = memStore(t)
	ensureTopicTable()

	g := types.NewJID("120363000000000001", types.GroupServer)
	by := types.NewJID("972500000001", types.DefaultUserServer)
	count := func() (n int) {
		globalStore.db.QueryRow("SELECT COUNT(*) FROM group_topics WHERE chat_jid = ?", g.String()).Scan(&n)
		return
	}
	if !storeTopic(nil, g, "1- ג׳לאל - פיתה שניצל", by, time.Now()) || count() != 1 {
		t.Fatal("first description not stored")
	}
	if storeTopic(nil, g, "1- ג׳לאל - פיתה שניצל", by, time.Now()) || count() != 1 {
		t.Fatal("an unchanged description must not add a row (the 30-minute poll repeats it)")
	}
	if !storeTopic(nil, g, "1- ג׳לאל - בגט שניצל", by, time.Now()) || count() != 2 {
		t.Fatal("a changed description must add a row")
	}
	var setBy string
	globalStore.db.QueryRow("SELECT set_by FROM group_topics ORDER BY rowid DESC LIMIT 1").Scan(&setBy)
	if setBy != "972500000001" {
		t.Fatalf("set_by = %q", setBy)
	}
	// rows older than ~3 months are dropped on the next change
	globalStore.db.Exec("UPDATE group_topics SET seen_at = ? WHERE rowid = 1", time.Now().AddDate(0, 0, -topicKeepDays-1))
	storeTopic(nil, g, "חדש", by, time.Time{})
	if count() != 2 {
		t.Fatalf("old row not pruned: %d rows", count())
	}
}

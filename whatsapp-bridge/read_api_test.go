package main

import (
	"database/sql"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func TestReadAPIUsesBoundedAllowListedQueries(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	_, err = db.Exec(`CREATE TABLE chats(jid TEXT,name TEXT,last_message_time TEXT);
		CREATE TABLE messages(id TEXT,chat_jid TEXT,sender TEXT,content TEXT,timestamp TEXT,is_from_me INTEGER,media_type TEXT);
		INSERT INTO chats VALUES('g@g.us','Group','2026-01-02T00:00:00Z');
		INSERT INTO messages VALUES('m1','g@g.us','owner','hello','2026-01-02T00:00:00Z',1,'');`)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := executeRead(db, readRequest{Kind: "messages", ChatJID: "g@g.us", Limit: 900})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0]["id"] != "m1" {
		t.Fatalf("unexpected rows: %#v", rows)
	}
	if _, err := executeRead(db, readRequest{Kind: "arbitrary_sql"}); err == nil {
		t.Fatal("unknown query kind must fail")
	}
}

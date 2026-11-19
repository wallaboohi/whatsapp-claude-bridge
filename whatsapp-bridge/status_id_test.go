package main

import (
	"testing"
	"time"
)

// Owner 19.11: the send result carries the id WhatsApp assigned; the agent uses it for the status message.
func TestSentIDFromResult(t *testing.T) {
	cases := map[string]string{
		"Message sent to 120363@g.us id=ABC123":           "ABC123",
		"Message sent to x id=3EB0A1B2 ":                "3EB0A1B2",
		"Message sent to x id=ABCD.EF-01_23:45":         "ABCD.EF-01_23:45",
		"Error sending message: boom":                     "",
		"Message sent to x id=   ":                        "",
		"":                                                "",
	}
	for in, want := range cases {
		if got := sentID(in); got != want {
			t.Fatalf("sentID(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestMarkLocalEdit(t *testing.T) {
	s := memStore(t)
	chat := "c@g.us"
	s.StoreMessage("m1", chat, "u", "original", time.Now(), false, "", "", "", nil, nil, nil, 0)
	markLocalEdit(s, chat, "m1", "new text")
	var content string
	var edited int
	if err := s.db.QueryRow("SELECT content, edited FROM messages WHERE id = 'm1'").Scan(&content, &edited); err != nil {
		t.Fatal(err)
	}
	if content != "new text" || edited != 1 {
		t.Fatalf("edit not recorded: content=%q edited=%d", content, edited)
	}
	// a message in another chat with the same id must not be touched
	markLocalEdit(s, "other@g.us", "m1", "wrong chat")
	s.db.QueryRow("SELECT content FROM messages WHERE id = 'm1'").Scan(&content)
	if content != "new text" {
		t.Fatalf("edit landed on the wrong chat: %q", content)
	}
	markLocalEdit(nil, chat, "m1", "no store") // never panics when the bridge has no store yet

	// same-user fallback for a phone/LID JID mismatch (same user, different server form), but never across different users
	dm := "972500000001@s.whatsapp.net"
	s.StoreMessage("m2", dm, "u", "original", time.Now(), false, "", "", "", nil, nil, nil, 0)
	markLocalEdit(s, "972500000001@c.us", "m2", "dm edited")
	s.db.QueryRow("SELECT content FROM messages WHERE id = 'm2'").Scan(&content)
	if content != "dm edited" {
		t.Fatalf("same-user fallback did not land: %q", content)
	}
	markLocalEdit(s, "972599999999@s.whatsapp.net", "m2", "wrong user")
	s.db.QueryRow("SELECT content FROM messages WHERE id = 'm2'").Scan(&content)
	if content != "dm edited" {
		t.Fatalf("edit landed on a different user: %q", content)
	}
}

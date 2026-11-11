package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

func TestMediaFilenameIsUniquePerMessage(t *testing.T) {
	a := mediaFilename("ABC123", "image", "image_20261105_111815.jpg")
	b := mediaFilename("ABC124", "image", "image_20261105_111815.jpg")
	if a == b {
		t.Fatalf("two messages in the same second got the same file name %q", a)
	}
	if a != "image_ABC123.jpg" {
		t.Fatalf("unexpected name %q", a)
	}
	if got := mediaFilename("X1", "document", "report.pdf"); got != "X1_report.pdf" {
		t.Fatalf("documents keep their name with the id prefix, got %q", got)
	}
	if got := mediaFilename("", "image", "image_1.jpg"); got != "image_1.jpg" {
		t.Fatalf("no id keeps the original name, got %q", got)
	}
}

func TestLooksLikeBlock(t *testing.T) {
	for _, s := range []string{"server returned error 463", "server returned error 475", "server returned error 429",
		"rate-overlimit", "429 too many requests"} {
		if !looksLikeBlock(errors.New(s)) {
			t.Errorf("%q should be treated as throttling", s)
		}
	}
	// per-chat errors must not pause every send
	for _, s := range []string{"context deadline exceeded", "websocket closed", "account blocked", "contact blocked you",
		"not-authorized", "403 forbidden", "server returned error 403", "server returned error 406", "not in group",
		"info query returned status 404"} {
		if looksLikeBlock(errors.New(s)) {
			t.Errorf("%q should not pause sending", s)
		}
	}
	if !looksLikeBlock(fmt.Errorf("send: %w", whatsmeow.ErrIQRateOverLimit)) {
		t.Error("typed rate-overlimit IQ error must pause")
	}
	if looksLikeBlock(whatsmeow.ErrIQForbidden) || looksLikeBlock(whatsmeow.ErrIQNotAuthorized) || looksLikeBlock(nil) {
		t.Error("forbidden / not-authorized / nil must not pause")
	}
	if !looksLikeBlock(fmt.Errorf("%w %d", whatsmeow.ErrServerReturnedError, 463)) {
		t.Error("typed server error 463 must pause")
	}
}

func TestInList(t *testing.T) {
	jid := types.NewJID("972500000001", types.DefaultUserServer)
	if !inList([]string{"972500000001"}, jid) || !inList([]string{"972500000001@s.whatsapp.net"}, jid) {
		t.Fatal("phone and full JID forms must both match")
	}
	if inList([]string{"972500000002"}, jid) {
		t.Fatal("other numbers must not match")
	}
}

func TestPolicyDraftByDefault(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"draft","owner":["972500000001@s.whatsapp.net"],"control_group":"1203630000@g.us"}`), 0644)

	if d, _ := checkPolicy(types.NewJID("972500000009", types.DefaultUserServer)); d != "draft" {
		t.Fatalf("a stranger must get a draft, got %q", d)
	}
	if d, _ := checkPolicy(types.NewJID("972500000001", types.DefaultUserServer)); d != "send" {
		t.Fatalf("the owner must be sent to directly, got %q", d)
	}
	if d, _ := checkPolicy(types.NewJID("1203630000", types.GroupServer)); d != "send" {
		t.Fatalf("the control group must be sent to directly, got %q", d)
	}
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"auto","owner":["972500000001"],"paused_until":"2999-01-01T00:00:00Z"}`), 0644)
	if d, _ := checkPolicy(types.NewJID("972500000009", types.DefaultUserServer)); d != "paused" {
		t.Fatalf("a pause must win over auto mode, got %q", d)
	}
}

func TestEnvInt(t *testing.T) {
	os.Setenv("WA_TEST_INT", "42")
	if envInt("WA_TEST_INT", 1) != 42 || envInt("WA_TEST_MISSING", 7) != 7 {
		t.Fatal("envInt")
	}
}

func TestGroupName(t *testing.T) {
	if n, err := groupName("  "); err != nil || n != "Claude" {
		t.Fatalf("empty name: %q %v", n, err)
	}
	if n, err := groupName("Claude test (אפשר למחוק)"); err != nil || n != "Claude test (אפשר למחוק)" {
		t.Fatalf("hebrew name: %q %v", n, err)
	}
	if _, err := groupName("Claude \xf0\xe9"); err == nil {
		t.Fatal("invalid UTF-8 should be rejected")
	}
	if _, err := groupName("תחזוק ובניית הגשר עם קלאוד"); err != nil {
		t.Fatalf("a 26-character Hebrew name must be accepted: %v", err)
	}
	if _, err := groupName(strings.Repeat("a", 101)); err == nil {
		t.Fatal("101 chars should be rejected (WhatsApp limit is 100)")
	}
}

func TestDocMime(t *testing.T) {
	if docMime("pdf") != "application/pdf" || docMime("xlsx") == "application/octet-stream" || docMime("bin") != "application/octet-stream" {
		t.Fatal("document MIME types (owner 11.11: a PDF must be application/pdf so phones open it)")
	}
}

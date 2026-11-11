package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow/types"
)

func TestOneShotApprovalSendsOnce(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"draft","owner":["972500000001@s.whatsapp.net"],"control_group":"1203630000@g.us"}`), 0644)
	who := types.NewJID("972500000009", types.DefaultUserServer)
	if d, _ := checkPolicyApproved(who, true); d != "send" {
		t.Fatalf("an approved draft must be sent, got %q", d)
	}
	if d, _ := checkPolicy(who); d != "draft" {
		t.Fatalf("one approval = one message: the next send must be a draft again, got %q", d)
	}
}

func TestOneShotApprovalDoesNotBeatPause(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"draft","paused_until":"2999-01-01T00:00:00Z"}`), 0644)
	who := types.NewJID("972500000008", types.DefaultUserServer)
	if d, _ := checkPolicyApproved(who, true); d != "paused" {
		t.Fatalf("an approval must not bypass a pause, got %q", d)
	}
}

func TestDraftCardText(t *testing.T) {
	who := types.NewJID("972500000009", types.DefaultUserServer)
	c := draftCardText(21, "דני", who, "שלום עולם", "")
	lines := strings.Split(c, "\n")
	if strings.Contains(lines[0], "#") || !strings.Contains(lines[0], "דני") {
		t.Fatalf("first line: recipient, no draft number: %q", lines[0])
	}
	if lines[2] != "שלום עולם" {
		t.Fatalf("the text to approve comes right after the title: %q", lines[2])
	}
	if !strings.Contains(lines[4], "לחיצה ארוכה") || !strings.Contains(lines[4], "✅") || !strings.Contains(lines[4], "❌") {
		t.Fatalf("approval hint right after the text: %q", lines[4])
	}
	if !strings.HasPrefix(lines[len(lines)-1], "https://wa.me/") {
		t.Fatalf("the link comes last: %q", lines[len(lines)-1])
	}
	if !strings.Contains(c, "https://wa.me/972500000009?text=%D7%A9%D7%9C%D7%95%D7%9D%20") {
		t.Fatalf("missing wa.me link with %%20 spaces:\n%s", c)
	}
	if !strings.Contains(c, "שלום עולם") {
		t.Fatal("the message text must be in the card")
	}
	// owner 10.11: a long Hebrew text makes a link the phone cuts (~400 chars) -> no link, the text comes separately
	long := strings.Repeat("ציון, בקובץ של החברה אין דיווח לינה. ", 3)
	lc := draftCardText(22, "ציון", who, long, "")
	if strings.Contains(lc, "?text=") || strings.Contains(lc, "להעתקה") || !needsCopyText(who, long, "") {
		t.Fatalf("long text: no pre-typed link and no second copy (owner 11.11):\n%s", lc)
	}
	if needsCopyText(who, "שלום עולם", "") {
		t.Fatal("a short text keeps its link")
	}
	g := draftCardText(0, "קבוצה", types.NewJID("1203630000", types.GroupServer), "x", "C:/f.jpg")
	if strings.Contains(g, "wa.me/1203630000?text") || !strings.Contains(g, "📎") {
		t.Fatalf("group/media card must not carry a prefilled link and must mention the file:\n%s", g)
	}
}

func TestConnectedGroupsAreSentToDirectly(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(dir)
	os.MkdirAll(filepath.Join(dir, "store"), 0755)
	os.WriteFile(filepath.Join(dir, "store", "send_policy.json"),
		[]byte(`{"mode":"draft","owner":["972500000001@s.whatsapp.net"],"control_group":"1203630000@g.us","control_groups":["1203631111@g.us"]}`), 0644)
	if d, _ := checkPolicy(types.NewJID("1203631111", types.GroupServer)); d != "send" {
		t.Fatalf("a connected extra group must get Claude's replies directly, got %q", d)
	}
	if d, _ := checkPolicy(types.NewJID("1203632222", types.GroupServer)); d != "draft" {
		t.Fatalf("an unconnected group must still be a draft, got %q", d)
	}
}

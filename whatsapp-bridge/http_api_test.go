package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"go.mau.fi/whatsmeow/types"
)

var registerOnce sync.Once

// every endpoint the bridge serves (kept in step with the handlers; a new endpoint must be added here)
var allEndpoints = []string{
	"/api/send", "/api/download", "/api/react", "/api/typing", "/api/status", "/api/policy", "/api/events",
	"/api/draft_decide", "/api/create_group", "/api/leave_group", "/api/join_group", "/api/list_groups",
	"/api/group_info", "/api/set_group", "/api/group_participants", "/api/group_photo", "/api/group_settings",
	"/api/group_invite", "/api/edit_message", "/api/delete_message", "/api/forward", "/api/pin_message", "/api/star",
	"/api/mark_read", "/api/pin_chat", "/api/send_poll", "/api/send_contact", "/api/post_status", "/api/set_about",
	"/api/block", "/api/history_sync",
}

func serve(t *testing.T, method, path, tok, body string) (code int, panicked bool) {
	t.Helper()
	h := withAuth(http.DefaultServeMux)
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	rec := httptest.NewRecorder()
	func() {
		defer func() {
			if recover() != nil {
				panicked = true
			}
		}()
		h.ServeHTTP(rec, req)
	}()
	return rec.Code, panicked
}

// Every endpoint is registered and protected: no token → 401; the API token reaches the handler, which rejects a
// malformed request on its own (without touching WhatsApp).
func TestEndpointsRegisteredAndAuthed(t *testing.T) {
	policyDir(t, `{"mode":"draft","owner":["972500000001"],"control_group":"1203630000@g.us"}`)
	registerOnce.Do(func() { registerAllAPI(nil, nil) })
	apiToken = "api-token-for-tests-aaaa"
	defer func() { apiToken = "" }()
	for _, p := range allEndpoints {
		if c, _ := serve(t, http.MethodPost, p, "", "{}"); c != http.StatusUnauthorized {
			t.Errorf("%s without a token: got %d, want 401", p, c)
		}
		if c, _ := serve(t, http.MethodPost, p, "wrong-token", "{}"); c != http.StatusUnauthorized {
			t.Errorf("%s with a wrong token: got %d, want 401", p, c)
		}
		if p == "/api/events" || p == "/api/status" || p == "/api/policy" {
			continue // streaming / live-status handlers: covered by the auth checks above
		}
		c, panicked := serve(t, http.MethodGet, p, apiToken, "not json")
		if panicked {
			t.Errorf("%s: malformed request with a valid token must be rejected before touching WhatsApp (panicked)", p)
			continue
		}
		if c == http.StatusUnauthorized || c == http.StatusNotFound {
			t.Errorf("%s with the API token: got %d (not registered or not reaching the handler)", p, c)
		}
		if c < 400 {
			t.Errorf("%s: a malformed request must be rejected, got %d", p, c)
		}
	}
	if c, _ := serve(t, http.MethodGet, "/api/policy", apiToken, ""); c != http.StatusOK {
		t.Errorf("GET /api/policy with the API token: got %d", c)
	}
}

// /api/send to someone who is not the owner is a draft, never a direct send (unit level: the live send needs a client).
func TestSendToOthersIsADraft(t *testing.T) {
	policyDir(t, `{"mode":"draft","owner":["972500000001"],"control_group":"1203630000@g.us"}`)
	for _, j := range []types.JID{types.NewJID("972500000009", types.DefaultUserServer), types.NewJID("1203639999", types.GroupServer)} {
		if d, _ := checkPolicy(j); d != "draft" {
			t.Errorf("%s: got %q, want draft", j, d)
		}
	}
	for _, j := range []types.JID{types.NewJID("972500000001", types.DefaultUserServer), types.NewJID("1203630000", types.GroupServer)} {
		if d, _ := checkPolicy(j); d != "send" {
			t.Errorf("owner / control group %s: got %q, want send", j, d)
		}
	}
}

func TestDraftCardMentionsScheduling(t *testing.T) {
	c := draftCardText(5, "דני", types.NewJID("972500000009", types.DefaultUserServer), "שלום", "")
	if lines := strings.Split(c, "\n"); !strings.Contains(lines[5], "שעה") {
		t.Fatalf("line 6 should explain scheduled sending, got %q", lines[5])
	}
}

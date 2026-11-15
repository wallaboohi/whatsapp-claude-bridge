package main

import "testing"

func TestCardChatFor(t *testing.T) {
	p := SendPolicy{ControlGroup: "main@g.us", ControlGroups: []string{"auto@g.us"}}
	for in, want := range map[string]string{"": "main@g.us", "auto@g.us": "auto@g.us", "main@g.us": "main@g.us", "stranger@g.us": "main@g.us"} {
		if got := p.cardChatFor(in); got != want {
			t.Errorf("cardChatFor(%q) = %q, want %q", in, got, want)
		}
	}
	if got := p.cardChatFor(); got != "main@g.us" {
		t.Errorf("no argument = %q", got)
	}
}

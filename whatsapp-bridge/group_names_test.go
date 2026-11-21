package main

import "testing"

func TestHostPrefix(t *testing.T) {
	for host, want := range map[string]string{"YB-PC-HW": "HW", "yb-pc-hw": "HW", "PERROOM": "PR", "SALON-PC": "SL", "LAP-YAKIR": "LAP", "OTHER": ""} {
		if got := hostPrefix(host); got != want {
			t.Fatalf("%s -> %q, want %q", host, got, want)
		}
	}
}

func TestWithHostPrefix(t *testing.T) {
	// 21.11: the laptop wrote LAP over HW; the desktop's bridge puts its own prefix back
	if got := withHostPrefix("LAP הייקו YB אוטומציה 4M-D", "HW"); got != "HW הייקו YB אוטומציה 4M-D" {
		t.Fatalf("LAP -> HW: %q", got)
	}
	// the old laptop form and the backups' prefixes are replaced too
	for _, old := range []string{"LP", "PR", "SL"} {
		if got := withHostPrefix(old+" גשר ויוי", "HW"); got != "HW גשר ויוי" {
			t.Fatalf("%s -> HW: %q", old, got)
		}
	}
	// already right: nothing to do
	if withHostPrefix("HW הייקו YB אוטומציה 4M-D", "HW") != "" {
		t.Fatal("a correct name must not be rewritten")
	}
	// a name that is not one of ours is never touched
	if withHostPrefix("משפחה", "HW") != "" || withHostPrefix("Team news", "HW") != "" || withHostPrefix("", "HW") != "" {
		t.Fatal("only names that start with a computer prefix are changed")
	}
	// an unknown computer changes nothing
	if withHostPrefix("LAP x", "") != "" {
		t.Fatal("unknown host: no change")
	}
}

func TestOwnerGroupsOnly(t *testing.T) {
	p := SendPolicy{ControlGroup: "1@g.us", ControlGroups: []string{"2@g.us", "1@g.us", "972500000001@s.whatsapp.net"}}
	got := ownerGroups(p)
	if len(got) != 2 || got[0] != "1@g.us" || got[1] != "2@g.us" {
		t.Fatalf("only the owner's groups, once each: %v", got)
	}
}

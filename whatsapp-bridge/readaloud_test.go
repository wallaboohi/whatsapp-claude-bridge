package main

import "testing"

func TestReadAloudGrantIsOneShotAndNarrow(t *testing.T) {
	grantReadAloud("972500000001@s.whatsapp.net", "M1")
	if consumeReadAloud("972500000002@s.whatsapp.net", "M1", "", "a.ogg") {
		t.Fatal("another chat must not use the grant")
	}
	if consumeReadAloud("972500000001@s.whatsapp.net", "M1", "text", "a.ogg") {
		t.Fatal("text is not a read-aloud voice note")
	}
	if consumeReadAloud("972500000001@s.whatsapp.net", "M1", "", "a.jpg") {
		t.Fatal("only a voice note")
	}
	if !consumeReadAloud("972500000001@s.whatsapp.net", "M1", "", "a.ogg") {
		t.Fatal("the reacted chat gets one voice note")
	}
	if consumeReadAloud("972500000001@s.whatsapp.net", "M1", "", "a.ogg") {
		t.Fatal("one reaction = one voice note")
	}
}

package main

import (
	"strings"
	"testing"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/proto"
)

func TestDescribeRichCoversNonTextTypes(t *testing.T) {
	cases := map[string]*waE2E.Message{
		"📍 מיקום: גדר · https://maps.google.com/?q=32.360000,35.510000": {LocationMessage: &waE2E.LocationMessage{
			Name: proto.String("גדר"), DegreesLatitude: proto.Float64(32.36), DegreesLongitude: proto.Float64(35.51)}},
		"👤 איש קשר: דנה · +972500000000": {ContactMessage: &waE2E.ContactMessage{DisplayName: proto.String("דנה"),
			Vcard: proto.String("BEGIN:VCARD\nTEL;type=CELL:+972500000000\nEND:VCARD")}},
		"🏷 מדבקה 😀":        {StickerMessage: &waE2E.StickerMessage{Emojis: proto.String("😀")}},
		"🔘 נבחר: כן, אגיע": {ButtonsResponseMessage: &waE2E.ButtonsResponseMessage{Response: &waE2E.ButtonsResponseMessage_SelectedDisplayText{SelectedDisplayText: "כן, אגיע"}}},
		"👁 סוד":            {ViewOnceMessage: &waE2E.FutureProofMessage{Message: &waE2E.Message{ImageMessage: &waE2E.ImageMessage{Caption: proto.String("סוד")}}}},
	}
	for want, m := range cases {
		if got := describeRich(m); got != want {
			t.Errorf("got %q want %q", got, want)
		}
	}
	if extractTextContent(&waE2E.Message{StickerMessage: &waE2E.StickerMessage{}}) == "" {
		t.Error("stickers must not be dropped any more")
	}
}

func TestBuildRich(t *testing.T) {
	msg, desc, err := buildRich("event", specialPayload{Name: "סיור גדר", Start: 1900000000, Address: "מחולה"})
	if err != nil || msg.GetEventMessage().GetName() != "סיור גדר" || len(msg.GetMessageContextInfo().GetMessageSecret()) != 32 ||
		!strings.HasPrefix(desc, "📅 אירוע: סיור גדר") {
		t.Fatalf("event: %v %q", err, desc)
	}
	if _, _, err = buildRich("event", specialPayload{Name: "x"}); err == nil {
		t.Error("event without start must fail")
	}
	msg, desc, err = buildRich("mention", specialPayload{Text: "תדווח ק\"מ", Mentions: []string{"+972500000001"}})
	if err != nil || !strings.HasPrefix(desc, "@972500000001 ") ||
		msg.GetExtendedTextMessage().GetContextInfo().GetMentionedJID()[0] != "972500000001@s.whatsapp.net" {
		t.Fatalf("mention: %v %q", err, desc)
	}
	if _, _, err = buildRich("mention", specialPayload{Text: "בלי טלפון"}); err == nil {
		t.Error("mention without a phone must fail")
	}
	if _, _, err = buildRich("location", specialPayload{Lat: 32.3, Lng: 35.5, Name: "שער"}); err != nil {
		t.Fatal(err)
	}
	if _, _, err = buildRich("nope", specialPayload{}); err == nil {
		t.Error("unknown kind must fail")
	}
}

func TestEventAnswerLine(t *testing.T) {
	r := &waE2E.EventResponseMessage{Response: waE2E.EventResponseMessage_GOING.Enum(), ExtraGuestCount: proto.Int32(2)}
	if got := eventAnswerLine(r, "סיור"); got != "📅 תשובה לאירוע: ✅ מגיע (+2) · סיור" {
		t.Errorf("got %q", got)
	}
}

func TestPollCreationV4Unwrapped(t *testing.T) {
	inner := &waE2E.Message{PollCreationMessageV3: &waE2E.PollCreationMessage{Name: proto.String("לינה השבוע")}}
	msg := &waE2E.Message{PollCreationMessageV4: &waE2E.FutureProofMessage{Message: inner}}
	if p := pollCreation(msg); p == nil || p.GetName() != "לינה השבוע" {
		t.Fatal("a V4 poll (FutureProof wrapper) must be read (owner 11.11: the weekly lodging poll was lost)")
	}
}

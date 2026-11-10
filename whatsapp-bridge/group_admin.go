package main

// Group administration + message edit/delete endpoints (all POST json, localhost only).
// Sending text to other people still goes through the send policy; these only manage groups / own messages.

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func adminHandler(path string, fn func(ctx context.Context, body map[string]any) (map[string]any, error)) {
	http.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&body) != nil {
			http.Error(w, "POST json", http.StatusBadRequest)
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		res, err := fn(ctx, body)
		if res == nil {
			res = map[string]any{}
		}
		res["success"] = err == nil
		if err != nil {
			res["error"] = err.Error()
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(res)
	})
}

func str(b map[string]any, k string) string { s, _ := b[k].(string); return s }

func jidList(b map[string]any, k string) ([]types.JID, error) {
	var out []types.JID
	raw, _ := b[k].([]any)
	for _, x := range raw {
		s, _ := x.(string)
		if !strings.Contains(s, "@") {
			s += "@s.whatsapp.net"
		}
		j, err := types.ParseJID(s)
		if err != nil {
			return nil, err
		}
		out = append(out, j)
	}
	return out, nil
}

func registerAdminAPI(client *whatsmeow.Client) {
	registerExtras2API(client)
	registerApprovalAPI(client)
	grp := func(b map[string]any) (types.JID, error) { return types.ParseJID(str(b, "jid")) }

	// {"jid","action":"add|remove|promote|demote","users":["9725..."]}
	adminHandler("/api/group_participants", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		g, err := grp(b)
		if err != nil {
			return nil, err
		}
		users, err := jidList(b, "users")
		if err != nil {
			return nil, err
		}
		if lp, cg := loadPolicy(), str(b, "jid"); (cg == lp.ControlGroup || inList(lp.ControlGroups, g)) && str(b, "action") == "remove" {
			return nil, errString("refusing to remove people from the control group")
		}
		res, err := client.UpdateGroupParticipants(ctx, g, users, whatsmeow.ParticipantChange(str(b, "action")))
		out := []map[string]any{}
		for _, p := range res {
			out = append(out, map[string]any{"jid": p.JID.String(), "error": p.Error})
		}
		return map[string]any{"results": out}, err
	})

	// {"jid","path":"image.jpg"} or {"jid","remove":true}
	adminHandler("/api/group_photo", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		g, err := grp(b)
		if err != nil {
			return nil, err
		}
		var data []byte
		if p := str(b, "path"); p != "" {
			if data, err = os.ReadFile(p); err != nil {
				return nil, err
			}
		}
		id, err := client.SetGroupPhoto(ctx, g, data)
		return map[string]any{"picture_id": id}, err
	})

	// {"jid"} (group or contact) → downloads the current full-size picture to store/pictures/<user>.jpg and returns its
	// path (owner 10.11: Claude could not see another group's icon). {"picture": false} when the chat has none.
	adminHandler("/api/get_picture", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		jid, err := types.ParseJID(str(b, "jid"))
		if err != nil || jid.User == "" {
			return nil, errString("need a chat jid")
		}
		info, err := client.GetProfilePictureInfo(ctx, jid, &whatsmeow.GetProfilePictureParams{})
		if err != nil {
			if errors.Is(err, whatsmeow.ErrProfilePictureNotSet) {
				return map[string]any{"picture": false}, nil
			}
			return nil, err
		}
		if info == nil || info.URL == "" {
			return map[string]any{"picture": false}, nil
		}
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, info.URL, nil)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			return nil, errString("picture download failed: " + resp.Status)
		}
		data, err := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
		if err != nil {
			return nil, err
		}
		if err := os.MkdirAll(filepath.Join("store", "pictures"), 0755); err != nil {
			return nil, err
		}
		path, _ := filepath.Abs(filepath.Join("store", "pictures", jid.User+".jpg"))
		if err := os.WriteFile(path, data, 0644); err != nil {
			return nil, err
		}
		return map[string]any{"picture": true, "path": path, "picture_id": info.ID, "bytes": len(data)}, nil
	})

	// {"jid","announce":bool,"locked":bool,"approval":bool,"members_can_add":bool} — only given keys are changed
	adminHandler("/api/group_settings", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		g, err := grp(b)
		if err != nil {
			return nil, err
		}
		if v, ok := b["announce"].(bool); ok {
			if err = client.SetGroupAnnounce(ctx, g, v); err != nil {
				return nil, err
			}
		}
		if v, ok := b["locked"].(bool); ok {
			if err = client.SetGroupLocked(ctx, g, v); err != nil {
				return nil, err
			}
		}
		if v, ok := b["approval"].(bool); ok {
			if err = client.SetGroupJoinApprovalMode(ctx, g, v); err != nil {
				return nil, err
			}
		}
		if v, ok := b["members_can_add"].(bool); ok {
			mode := types.GroupMemberAddModeAdmin
			if v {
				mode = types.GroupMemberAddModeAllMember
			}
			if err = client.SetGroupMemberAddMode(ctx, g, mode); err != nil {
				return nil, err
			}
		}
		if v, ok := b["disappearing_seconds"].(float64); ok {
			if err = client.SetDisappearingTimer(ctx, g, time.Duration(v)*time.Second, time.Now()); err != nil {
				return nil, err
			}
		}
		return nil, nil
	})

	// {"jid","reset":bool} -> invite link
	adminHandler("/api/group_invite", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		g, err := grp(b)
		if err != nil {
			return nil, err
		}
		reset, _ := b["reset"].(bool)
		link, err := client.GetGroupInviteLink(ctx, g, reset)
		return map[string]any{"link": link}, err
	})

	// {"link":"https://chat.whatsapp.com/CODE"} — join a group
	adminHandler("/api/join_group", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		code := str(b, "link")
		code = code[strings.LastIndex(code, "/")+1:]
		j, err := client.JoinGroupWithLink(ctx, code)
		return map[string]any{"jid": j.String()}, err
	})

	// {} -> all joined groups
	adminHandler("/api/list_groups", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		gs, err := client.GetJoinedGroups(ctx)
		out := []map[string]any{}
		for _, g := range gs {
			admin := false
			for _, p := range g.Participants {
				if (p.JID.User == client.Store.ID.User || p.LID.User == client.Store.LID.User) && (p.IsAdmin || p.IsSuperAdmin) {
					admin = true
				}
			}
			out = append(out, map[string]any{"jid": g.JID.String(), "name": g.Name, "members": len(g.Participants), "i_am_admin": admin})
		}
		return map[string]any{"groups": out}, err
	})

	// {"jid","id","text"} — edit one of my own messages
	adminHandler("/api/edit_message", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		msg := client.BuildEdit(chat, types.MessageID(str(b, "id")), &waE2E.Message{Conversation: proto.String(str(b, "text"))})
		_, err = client.SendMessage(ctx, chat, msg)
		return nil, err
	})

	// {"jid","id","sender":optional jid of author (group admins deleting others)} — delete for everyone
	adminHandler("/api/delete_message", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		chat, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		sender := types.EmptyJID
		if s := str(b, "sender"); s != "" {
			if sender, err = types.ParseJID(s); err != nil {
				return nil, err
			}
		}
		_, err = client.SendMessage(ctx, chat, client.BuildRevoke(chat, sender, types.MessageID(str(b, "id"))))
		return nil, err
	})

	// {"jid","action":"block|unblock"}
	adminHandler("/api/block", func(ctx context.Context, b map[string]any) (map[string]any, error) {
		j, err := types.ParseJID(str(b, "jid"))
		if err != nil {
			return nil, err
		}
		act := "block"
		if str(b, "action") == "unblock" {
			act = "unblock"
		}
		_, err = client.UpdateBlocklist(ctx, j, events.BlocklistChangeAction(act), "")
		return nil, err
	})
}

type errString string

func (e errString) Error() string { return string(e) }

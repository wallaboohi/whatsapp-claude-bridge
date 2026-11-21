package main

// The computer prefix of the owner's group names is kept right by the BRIDGE itself (owner 21.11.2026 02:00).
//
// The first word of each connected group's name says which computer hosts the bridge: HW desktop, PR bedroom, SL living room,
// LAP laptop. The bridge knows its own computer, so it does this alone, with no help from the agent or any AI: after it connects
// and then every minute it asks WhatsApp for the real name of each of the owner's groups (the control groups of the send policy)
// and, when the first word is a computer prefix that is not this computer's, replaces just that word. It never trusts its own
// local chats table, which stays stale while the bridge is disconnected: the laptop renamed the groups to "LAP ..." during a
// hand-over and the desktop never put them back. The rest of the name (the model, the owner's words) is left to the agent.

import (
	"context"
	"os"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const groupNameEvery = time.Minute
const groupNameMinGap = 30 * time.Second // per group: never hammer WhatsApp with renames

var computerPrefixes = map[string]bool{"LAP": true, "LP": true, "HW": true, "SL": true, "PR": true, "SA": true, "PE": true}

// hostPrefix: this computer's prefix from its Windows host name ("" = unknown computer: the bridge changes nothing).
func hostPrefix(hostname string) string {
	switch strings.ToUpper(strings.TrimSpace(hostname)) {
	case "YB-PC-HW":
		return "HW"
	case "PERROOM":
		return "PR"
	case "SALON-PC":
		return "SL"
	case "LAP-YAKIR":
		return "LAP"
	}
	return ""
}

// withHostPrefix: the name with its computer prefix replaced by this computer's, or "" when nothing must change (the name is
// already right, has no computer prefix, or this computer is unknown).
func withHostPrefix(name, prefix string) string {
	words := strings.Fields(name)
	if prefix == "" || len(words) == 0 || !computerPrefixes[strings.ToUpper(words[0])] || words[0] == prefix {
		return ""
	}
	words[0] = prefix
	return strings.Join(words, " ")
}

// ownerGroups: the groups the owner talks to the bridge in (the only ones whose name the bridge touches).
func ownerGroups(p SendPolicy) []string {
	var out []string
	add := func(j string) {
		if strings.HasSuffix(j, "@g.us") {
			for _, o := range out {
				if o == j {
					return
				}
			}
			out = append(out, j)
		}
	}
	add(p.ControlGroup)
	for _, g := range p.ControlGroups {
		add(g)
	}
	return out
}

var groupNameLastSet = map[string]time.Time{}

func reconcileGroupNames(client *whatsmeow.Client, logger waLog.Logger) {
	host, err := os.Hostname()
	if err != nil {
		return
	}
	prefix := hostPrefix(host)
	if prefix == "" {
		return
	}
	for _, jidStr := range ownerGroups(loadPolicy()) {
		if last, ok := groupNameLastSet[jidStr]; ok && time.Since(last) < groupNameMinGap {
			continue
		}
		jid, err := types.ParseJID(jidStr)
		if err != nil || jid.Server != types.GroupServer {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		info, err := client.GetGroupInfo(ctx, jid)
		if err == nil {
			if fix := withHostPrefix(info.Name, prefix); fix != "" {
				groupNameLastSet[jidStr] = time.Now()
				if err := client.SetGroupName(ctx, jid, fix); err != nil {
					logger.Warnf("Group name prefix fix failed for %s: %v", jidStr, err)
				} else {
					logger.Infof("Group name prefix set by the bridge (%s hosts it): %s %q -> %q", prefix, jidStr, info.Name, fix)
					if globalStore != nil {
						globalStore.db.Exec("UPDATE chats SET name = ? WHERE jid = ?", fix, jidStr)
					}
				}
			}
		}
		cancel()
	}
}

// watchGroupNames: the first check 15 s after the connection (the groups are synced by then), then every minute while connected.
func watchGroupNames(client *whatsmeow.Client, logger waLog.Logger) {
	go func() {
		time.Sleep(15 * time.Second)
		for {
			if client.IsConnected() {
				reconcileGroupNames(client, logger)
			}
			time.Sleep(groupNameEvery)
		}
	}()
}

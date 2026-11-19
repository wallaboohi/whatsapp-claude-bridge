package main

// Session lease (owner 18.11.2026): the four PCs share ONE WhatsApp identity (the desktop's linked device). Its session
// file (store/whatsapp.db) moves from PC to PC along the backup chain; only the PC that holds it may connect. Two copies
// connected at once fight ("stream replaced") and corrupt the encryption state, so the bridge itself obeys a local lease:
//
//   store/lease_mode   exists -> lease mode is on for this PC (no file: the bridge runs as before)
//   store/lease_grant  written by the agent when this PC holds the identity. Valid only if written after the last boot
//                      (a PC that crashed and comes back must not reconnect with a stale session while another PC holds it).
//
// At start-up the bridge waits (before opening the session database) until a valid grant exists. While running it
// checks every second: grant removed -> disconnect cleanly and exit (the run-bridge loop then waits at the gate).
// Owner 18.11 19:42: every hand-over in the chain within 7 seconds, so both checks run every second (was 3 s), and in
// lease mode a "stream replaced" (another PC connected with the identity) makes this bridge let go at once instead of
// reconnecting in 2 minutes and fighting over the connection (leaseYield).

import (
	"os"
	"syscall"
	"time"

	"go.mau.fi/whatsmeow"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const leaseModeFile = "store/lease_mode"
const leaseGrantFile = "store/lease_grant"

// leasePoll: how often the gate and the running bridge look at the grant (owner 18.11 19:42: 7-second hand-overs)
const leasePoll = time.Second

func bootTime() time.Time {
	k := syscall.NewLazyDLL("kernel32.dll").NewProc("GetTickCount64")
	if k.Find() != nil {
		return time.Time{}
	}
	ms, _, _ := k.Call()
	return time.Now().Add(-time.Duration(ms) * time.Millisecond)
}

func leaseOn() bool {
	_, err := os.Stat(leaseModeFile)
	return err == nil
}

// leaseValid: grant exists and was written after this boot (with a minute of slack for clock rounding).
func leaseValid() bool {
	st, err := os.Stat(leaseGrantFile)
	if err != nil {
		return false
	}
	return st.ModTime().After(bootTime().Add(-time.Minute))
}

func waitForLease(logger waLog.Logger) {
	if !leaseOn() {
		return
	}
	last := time.Time{}
	for !leaseValid() {
		if time.Since(last) > time.Minute {
			logger.Infof("Session lease: this PC does not hold the WhatsApp identity now - waiting (store/lease_grant)")
			setLeaseStatus("waiting")
			last = time.Now()
		}
		time.Sleep(leasePoll)
	}
	logger.Infof("Session lease granted - connecting")
	setLeaseStatus("holding")
}

func setLeaseStatus(s string) {
	os.WriteFile("store/lease_status", []byte(s+" "+time.Now().Format(time.RFC3339)), 0644)
}

func watchLease(client *whatsmeow.Client, logger waLog.Logger) {
	if !leaseOn() {
		return
	}
	go func() {
		for {
			time.Sleep(leasePoll)
			if leaseOn() && !leaseValid() {
				logger.Warnf("Session lease revoked - disconnecting and exiting")
				setLeaseStatus("released")
				client.Disconnect()
				time.Sleep(500 * time.Millisecond)
				os.Exit(0)
			}
		}
	}()
}

// leaseYield: WhatsApp moved the connection to another PC holding the same identity ("stream replaced"). In lease mode this
// PC lets go for good: the grant goes (the agent decides who gets it next) and the status says why.
func leaseYield() bool {
	if !leaseOn() {
		return false
	}
	os.Remove(leaseGrantFile)
	setLeaseStatus("replaced")
	return true
}

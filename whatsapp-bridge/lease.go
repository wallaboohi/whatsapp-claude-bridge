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
// checks every few seconds: grant removed -> disconnect cleanly and exit (the run-bridge loop then waits at the gate).

import (
	"os"
	"syscall"
	"time"

	"go.mau.fi/whatsmeow"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const leaseModeFile = "store/lease_mode"
const leaseGrantFile = "store/lease_grant"

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
		time.Sleep(3 * time.Second)
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
			time.Sleep(3 * time.Second)
			if leaseOn() && !leaseValid() {
				logger.Warnf("Session lease revoked - disconnecting and exiting")
				setLeaseStatus("released")
				client.Disconnect()
				time.Sleep(time.Second)
				os.Exit(0)
			}
		}
	}()
}

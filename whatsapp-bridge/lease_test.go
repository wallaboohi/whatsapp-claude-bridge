package main

import (
	"os"
	"testing"
	"time"
)

func TestLeaseGate(t *testing.T) {
	dir := t.TempDir()
	old, _ := os.Getwd()
	defer os.Chdir(old)
	os.Chdir(dir)
	os.MkdirAll("store", 0755)
	if leaseOn() {
		t.Fatal("no lease_mode file: lease must be off")
	}
	os.WriteFile(leaseModeFile, []byte("1"), 0644)
	if !leaseOn() || leaseValid() {
		t.Fatal("lease on, no grant: not valid")
	}
	os.WriteFile(leaseGrantFile, []byte("x"), 0644)
	if !leaseValid() {
		t.Fatal("fresh grant must be valid")
	}
	before := bootTime().Add(-time.Hour)
	os.Chtimes(leaseGrantFile, before, before)
	if leaseValid() {
		t.Fatal("grant from before the boot must not be valid")
	}
}

func TestLeaseYield(t *testing.T) {
	dir := t.TempDir()
	old, _ := os.Getwd()
	defer os.Chdir(old)
	os.Chdir(dir)
	os.MkdirAll("store", 0755)
	if leaseYield() {
		t.Fatal("no lease mode: nothing to yield")
	}
	os.WriteFile(leaseModeFile, []byte("1"), 0644)
	os.WriteFile(leaseGrantFile, []byte("x"), 0644)
	if !leaseYield() || leaseValid() {
		t.Fatal("stream replaced in lease mode: the grant must be gone")
	}
	b, _ := os.ReadFile("store/lease_status")
	if len(b) < 8 || string(b[:8]) != "replaced" {
		t.Fatalf("status: %q", b)
	}
}

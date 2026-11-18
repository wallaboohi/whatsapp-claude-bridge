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

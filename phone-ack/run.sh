#!/data/data/com.termux/files/usr/bin/sh
# phone-ack launcher (owner 20.11.2026): keeps the helper alive in Termux; started by Termux:Boot.
export SSL_CERT_FILE=/data/data/com.termux/files/usr/etc/tls/cert.pem
export PATH=$PATH:/data/data/com.termux/files/usr/bin
cd ~/phone-ack || exit 1
termux-wake-lock 2>/dev/null
pgrep -f "phone-ack/phone-ack" >/dev/null && exit 0
while true; do
  ./phone-ack -dir ~/phone-ack >> ~/phone-ack/run.log 2>&1
  tail -n 300 ~/phone-ack/run.log > ~/phone-ack/run.log.t && mv ~/phone-ack/run.log.t ~/phone-ack/run.log
  sleep 5
done

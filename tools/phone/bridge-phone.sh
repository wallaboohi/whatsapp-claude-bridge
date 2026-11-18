#!/data/data/com.termux/files/usr/bin/sh
# Bridge phone helper (owner 19.11.2026): turns Tailscale on/off on request of the bridge, over ntfy (plain internet,
# works while Tailscale is off). Messages on the private topic: "up" | "down" | "ping" | "sshd".
# Optional idle off: ~/.bridge_idle_min = minutes with no "up" -> Tailscale off (0 / missing = never).
TOPIC=$(cat ~/.bridge_topic)
LOG=~/bridge-phone.log
TS="-n com.tailscale.ipn/.IPNReceiver"
log() { echo "$(date '+%F %T') $*" >> $LOG; tail -n 500 $LOG > $LOG.t && mv $LOG.t $LOG; }
reply() { curl -s -m 15 -d "$*" "https://ntfy.sh/$TOPIC-ack" > /dev/null 2>&1; }
pgrep -f "bridge-phone.sh idle" > /dev/null || nohup sh "$0" idle > /dev/null 2>&1 &
if [ "$1" = "idle" ]; then
  while true; do
    sleep 60
    M=$(cat ~/.bridge_idle_min 2>/dev/null || echo 0)
    [ "${M:-0}" -gt 0 ] 2>/dev/null || continue
    L=$(cat ~/.bridge_last_up 2>/dev/null || echo 0)
    if [ $(( $(date +%s) - L )) -gt $(( M * 60 )) ] && [ ! -f ~/.bridge_idle_off ]; then
      am broadcast -a com.tailscale.ipn.DISCONNECT_VPN $TS > /dev/null 2>&1; touch ~/.bridge_idle_off; log "idle off after $M min"
    fi
  done
fi
log "listener start"
while true; do
  curl -sN -m 3600 "https://ntfy.sh/$TOPIC/raw" 2>/dev/null | while read -r line; do
    case "$line" in
      up*)  am broadcast -a com.tailscale.ipn.CONNECT_VPN $TS > /dev/null 2>&1; date +%s > ~/.bridge_last_up; rm -f ~/.bridge_idle_off
            pgrep -x sshd > /dev/null || sshd; log "up"; reply "up-ok $(date +%T)";;
      down) am broadcast -a com.tailscale.ipn.DISCONNECT_VPN $TS > /dev/null 2>&1; log "down"; reply "down-ok $(date +%T)";;
      ping) reply "pong $(date +%T) bat=$(cat /sys/class/power_supply/battery/capacity 2>/dev/null)";;
      sshd) pgrep -x sshd > /dev/null || sshd; reply "sshd-ok";;
      *) [ -n "$line" ] && log "ignored: $line";;
    esac
  done
  sleep 5
done

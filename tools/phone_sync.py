"""The phone's data folder (owner 11.11): a local copy of the phone's WhatsApp media and documents, kept up to date once a
day over adb (Tailscale or home Wi-Fi; read-only on the phone), and a lookup that recovers the file of a WhatsApp message
the bridge could not download (old media: 410 / invalid media hmac) — the phone keeps the decrypted file, matched exactly
by the message's file_sha256.

Usage (from the tool root):
  uv run -q --no-project python -I -X utf8 tools/phone_sync.py status            phone reachable? network? last sync
  uv run -q --no-project python -I -X utf8 tools/phone_sync.py sync [--any-net]  new files only; on mobile data only
                                                                                 files under 2 MB unless --any-net
  uv run -q --no-project python -I -X utf8 tools/phone_sync.py find <message_id> [chat_jid]
                                                                                 the message's file -> bridge store path

Config (agent/config.json "phone"): {"adb": "100.115.100.33:5555", "data_dir": "~/phone-data"}. DCIM / camera photos are
not copied (owner approval needed).
"""
import base64
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    CFG = (json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8")).get("phone") or {})
except (OSError, ValueError):
    CFG = {}
DEVICE = CFG.get("adb") or "100.115.100.33:5555"
DATA = os.path.expanduser(CFG.get("data_dir") or "~/phone-data")
STATE = os.path.join(DATA, "_sync_state.json")
WA = "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"
FOLDERS = [f"{WA}/WhatsApp Images", f"{WA}/WhatsApp Video", f"{WA}/WhatsApp Documents", f"{WA}/WhatsApp Voice Notes",
           f"{WA}/WhatsApp Audio", f"{WA}/WhatsApp Video Notes", f"{WA}/WhatsApp Animated Gifs", "/sdcard/Documents",
           "/sdcard/Download"]
BY_TYPE = {"image": ["WhatsApp Images"], "video": ["WhatsApp Video", "WhatsApp Video Notes", "WhatsApp Animated Gifs"],
           "document": ["WhatsApp Documents"], "audio": ["WhatsApp Voice Notes", "WhatsApp Audio"],
           "sticker": [], "": []}
MOBILE_MAX = 2 * 1024 * 1024
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def adb_exe():
    return shutil.which("adb") or next(iter(glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools_*\platform-tools\adb.exe"))), "adb")


def adb(*args, timeout=120, binary=False):
    r = subprocess.run([adb_exe(), "-s", DEVICE, *args], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout if binary else r.stdout.decode("utf-8", "replace"))


def connect():
    """True when the phone answers. Over the network a dropped adb session is re-connected once."""
    rc, out = adb("shell", "echo ok", timeout=20)
    if rc == 0 and out.strip() == "ok":
        return True
    if ":" in DEVICE:
        subprocess.run([adb_exe(), "disconnect", DEVICE], capture_output=True, timeout=20, creationflags=NO_WINDOW)
        subprocess.run([adb_exe(), "connect", DEVICE], capture_output=True, timeout=30, creationflags=NO_WINDOW)
        rc, out = adb("shell", "echo ok", timeout=20)
    return rc == 0 and out.strip() == "ok"


def on_wifi():
    return "inet " in adb("shell", "ip -4 addr show wlan0")[1]


def listing(folder):
    """{phone path: (size, mtime)} for every file under folder (hidden folders and .nomedia skipped)."""
    q = folder.replace("'", "'\\''")
    rc, out = adb("exec-out", f"find '{q}' -type f ! -path '*/.*' -print0 2>/dev/null | xargs -0 -n 400 stat -c '%s|%Y|%n'",
                  timeout=600)
    files = {}
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            files[parts[2]] = (int(parts[0]), int(parts[1]))
    return files


def local_path(remote):
    return os.path.join(DATA, *remote[len("/sdcard/"):].split("/"))


def pull(paths):
    """Copy phone files into DATA, keeping their dates: one tar stream per batch (much faster than adb pull per file)."""
    done, failed = 0, 0
    os.makedirs(DATA, exist_ok=True)
    for i in range(0, len(paths), 300):
        batch = paths[i:i + 300]
        listfile = "/data/local/tmp/phone_sync.list"
        tmp = os.path.join(DATA, "_list.txt")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(p[len("/sdcard/"):] for p in batch) + "\n")
        adb("push", tmp, listfile, timeout=120)
        os.remove(tmp)
        proc = subprocess.Popen([adb_exe(), "-s", DEVICE, "exec-out", f"cd /sdcard && tar -cf - -T {listfile}"],
                                stdout=subprocess.PIPE, creationflags=NO_WINDOW)
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                for m in tar:
                    if not m.isfile():
                        continue
                    dst = os.path.join(DATA, *m.name.split("/"))
                    if not os.path.abspath(dst).startswith(os.path.abspath(DATA) + os.sep):
                        continue  # never write outside the data folder
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with tar.extractfile(m) as src, open(dst + ".part", "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.replace(dst + ".part", dst)
                    os.utime(dst, (m.mtime, m.mtime))
                    done += 1
        except (tarfile.TarError, OSError) as e:  # the stream broke (network / phone): the next run continues from here
            failed += 1
            print("batch interrupted:", e, flush=True)
            proc.kill()
            if failed >= 3 or not connect():
                break
        proc.wait()
    return done


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def sync(any_net=False):
    if not connect():
        st = load_state()
        st["last_try"] = dt.datetime.now().isoformat(timespec="seconds")
        st["last_error"] = "phone not reachable (Tailscale off / adb over network off)"
        save_state(st)
        print("phone not reachable:", DEVICE)
        return 2
    wifi = on_wifi()
    want, skipped = [], 0
    for folder in FOLDERS:
        for path, (size, mtime) in listing(folder).items():
            lp = local_path(path)
            if os.path.exists(lp) and os.path.getsize(lp) == size:
                continue
            if not wifi and not any_net and size > MOBILE_MAX:
                skipped += 1
                continue
            want.append(path)
    os.makedirs(DATA, exist_ok=True)
    got = pull(want) if want else 0
    if got < len(want):
        skipped += len(want) - got  # not copied this time (interrupted): counts as "not done yet"
    st = load_state()
    st.update({"last_sync": dt.datetime.now().isoformat(timespec="seconds"), "network": "wifi" if wifi else "mobile",
               "copied": got, "skipped_big_on_mobile": skipped, "last_error": ""})
    save_state(st)
    print(f"copied {got} new file(s) to {DATA}" + (f"; {skipped} big file(s) wait for Wi-Fi" if skipped else ""))
    return 3 if skipped else 0  # 3 = partial (mobile data): the agent tries again later today


def find(message_id, chat_jid=None):
    """Put the message's file where the bridge keeps media (store/<chat>/<filename>), taken from the phone's copy."""
    db = sqlite3.connect(f"file:{os.path.join(ROOT, 'whatsapp-bridge', 'store', 'messages.db')}?mode=ro", uri=True)
    q = "SELECT chat_jid, media_type, filename, file_length, file_sha256, timestamp FROM messages WHERE id = ?"
    row = db.execute(q + (" AND chat_jid = ?" if chat_jid else ""), (message_id, chat_jid) if chat_jid else (message_id,)).fetchone()
    if not row or not row[1]:
        print("no media message", message_id)
        return 1
    chat, mtype, filename, length, sha, ts = row
    dest = os.path.join(ROOT, "whatsapp-bridge", "store", chat.replace(":", "_"), filename)
    if os.path.exists(dest):
        print(dest)
        return 0
    if not sha:
        print("the message has no file hash; cannot be matched safely")
        return 1
    day = dt.datetime.fromisoformat(ts[:19])
    days = {(day + dt.timedelta(days=d)).strftime("%Y%m%d") for d in (-1, 0, 1, 2)}
    subdirs = BY_TYPE.get(mtype) or [os.path.basename(f) for f in FOLDERS[:7]]

    def candidates(files):  # same size; for WhatsApp-named files also the date in the name (IMG-20261012-WA0003.jpg)
        out = []
        for path, size in files:
            if size != length:
                continue
            name = os.path.basename(path)
            if name[:4] in ("IMG-", "VID-", "PTT-", "AUD-") and not any(d in name for d in days):
                continue
            out.append(path)
        return out

    def matches(p):
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).digest() == bytes(sha)

    local = []
    for sd in subdirs:
        for p in glob.glob(os.path.join(DATA, "Android", "media", "com.whatsapp", "WhatsApp", "Media", sd, "**", "*"), recursive=True):
            if os.path.isfile(p):
                local.append((p, os.path.getsize(p)))
    hit = next((p for p in candidates(local) if matches(p)), None)
    if not hit and connect():
        remote = []
        for sd in subdirs:
            remote += [(p, s) for p, (s, _) in listing(f"{WA}/{sd}").items()]
        cands = candidates(remote)
        if cands:
            pull(cands)
            hit = next((local_path(p) for p in cands if os.path.exists(local_path(p)) and matches(local_path(p))), None)
    if not hit:
        print("not found on the phone (deleted there, or never downloaded by the phone)")
        return 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(hit, dest)
    print(dest)
    return 0


def status():
    st = load_state()
    ok = connect()
    print(json.dumps({"device": DEVICE, "reachable": ok, "wifi": ok and on_wifi(), "data_dir": DATA, **st}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "sync":
        sys.exit(sync("--any-net" in sys.argv))
    if cmd == "find" and len(sys.argv) > 2:
        sys.exit(find(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    sys.exit(status())

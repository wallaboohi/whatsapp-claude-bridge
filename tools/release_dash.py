"""Build and publish a new version of the dashboard app (WhatsAppDash.exe).

  uv run --no-project python tools/release_dash.py 1.0.1 "מה השתנה"

Bumps dash/VERSION, builds with PyInstaller, copies releases/WhatsAppDash-<ver>.exe, writes releases/latest.json and
appends to releases/CHANGELOG.md. Installed apps see the update under "בדוק עדכונים".
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ver, notes = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
open(os.path.join(ROOT, "dash", "VERSION"), "w").write(ver + "\n")
r = ROOT.replace("\\", "/")
subprocess.run([shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe"), "run", "-q", "--no-project", "--python", "3.14", "--with", "pyinstaller", "--with", "pywebview",
                "--with", "pystray", "--with", "pillow", "pyinstaller", "--noconfirm", "--onefile", "--windowed", "--name", "WhatsAppDash",
                "--icon", f"{r}/dash/icon.ico", "--add-data", f"{r}/dash/static;dash/static", "--add-data", f"{r}/dash/VERSION;dash",
                "--add-data", f"{r}/dash/__init__.py;dash", "--add-data", f"{r}/dash/server.py;dash", "--add-data", f"{r}/dash/updater.py;dash",
                "--hidden-import", "dash.updater", "--hidden-import", "dash.server",
                "--distpath", "build/dist", "--workpath", "build/pyi", "--specpath", "build", "dash/app.py"], cwd=ROOT, check=True)
rel = os.path.join(ROOT, "releases")
os.makedirs(rel, exist_ok=True)
name = f"WhatsAppDash-{ver}.exe"
shutil.copy(os.path.join(ROOT, "build", "dist", "WhatsAppDash.exe"), os.path.join(rel, name))
json.dump({"version": ver, "file": name, "notes": notes, "date": dt.date.today().isoformat()},
          open(os.path.join(rel, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open(os.path.join(rel, "CHANGELOG.md"), "a", encoding="utf-8") as f:
    f.write(f"\n## {ver} — {dt.date.today():%d.%m.%Y}\n{notes or '—'}\n")
print("released", name)

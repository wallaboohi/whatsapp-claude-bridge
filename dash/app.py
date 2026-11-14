"""WhatsAppDash — desktop app of the WhatsApp ↔ Claude tool (built into WhatsAppDash.exe with PyInstaller).

Opens the dashboard in its own window (Edge WebView2), with a tray icon showing the connection state.
Works offline and without the agent: the dashboard reads the tool's local databases directly.
"""
import os
import socket
import sys
import threading
import time

if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)
else:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview  # noqa: E402

from dash import server  # noqa: E402

SHOW_PORT = 8779  # one app per user: a second launch asks the running one to show its window, then exits
_quitting = [False]


def already_running():
    try:
        with socket.create_connection(("127.0.0.1", SHOW_PORT), timeout=1) as c:
            c.sendall(b"show")
        return True
    except OSError:
        return False


def listen_for_show(window):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", SHOW_PORT))
    except OSError:
        return
    srv.listen(2)

    def loop():
        while True:
            conn, _ = srv.accept()
            with conn:
                if conn.recv(16) == b"show":
                    window.show()
                    window.restore()
    threading.Thread(target=loop, daemon=True).start()


def tray(window):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return

    def icon_img(color):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(31, 170, 89))
        d.ellipse((40, 40, 62, 62), fill=color, outline=(255, 255, 255), width=3)
        return img

    def show(icon=None, item=None):
        window.show()
        window.restore()

    def quit_app(icon, item):
        _quitting[0] = True
        icon.stop()
        window.destroy()

    def on_closing():
        # owner 10.11: closing the window used to destroy it, so the tray's "open" showed nothing afterwards —
        # now X only hides it; "יציאה" in the tray really quits
        if _quitting[0]:
            return True
        window.hide()
        return False

    window.events.closing += on_closing

    icon = pystray.Icon("WhatsAppDash", icon_img((150, 150, 150)), "ווצאפ ↔ Claude",
                        menu=pystray.Menu(pystray.MenuItem("פתח לוח בקרה", show, default=True), pystray.MenuItem("יציאה", quit_app)))
    icon.run_detached()

    def refresh():
        while True:
            st = server.bridge("/status")
            ok = not st.get("error") and st.get("connected")
            icon.icon = icon_img((69, 196, 122) if ok else (255, 107, 94))
            icon.title = "ווצאפ ↔ Claude — " + ("מחובר" if ok else "מנותק")
            time.sleep(30)

    threading.Thread(target=refresh, daemon=True).start()


def allow_mic_camera(window):
    """Owner 11.11: the composer's 🎤 and 📷 buttons. WebView2 asks for microphone / camera access; the dashboard's own
    local page (127.0.0.1) gets it at once, anything else keeps WebView2's default (ask)."""
    done = []

    def hook():
        if done:
            return
        done.append(1)
        try:
            from System import Action
            from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState

            def on_request(sender, args):
                if str(args.Uri).startswith("http://127.0.0.1:") and args.PermissionKind in (
                        CoreWebView2PermissionKind.Microphone, CoreWebView2PermissionKind.Camera):
                    args.State = CoreWebView2PermissionState.Allow

            def attach():
                window.native.browser.webview.CoreWebView2.PermissionRequested += on_request
            window.native.Invoke(Action(attach))
        except Exception as e:  # never block the dashboard over this; the browser's own prompt still works
            print("mic/camera permission hook failed:", e)
    window.events.loaded += hook  # fires once the page (and so CoreWebView2) is ready


def main():
    if already_running():
        return
    port = server.start_free_port()
    url = f"http://127.0.0.1:{port}/"
    try:  # owner 14.11: the agent serves the dashboard from the tool's live code; prefer it, so every fix reaches the window without a new app
        import urllib.request
        if urllib.request.urlopen("http://127.0.0.1:8770/brief", timeout=3).status == 200:
            url = "http://127.0.0.1:8770/"
    except Exception:
        pass  # agent down: the app's own copy still works offline
    window = webview.create_window(f"ווצאפ ↔ Claude · לוח בקרה {server.VERSION}{' · ' + server.BUILD if server.BUILD else ''}", url,
                                   width=1360, height=900, min_size=(760, 600), text_select=True)
    listen_for_show(window)
    allow_mic_camera(window)
    webview.start(tray, window, gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(os.environ.get("LOCALAPPDATA", "."), "WhatsAppDash", "webview"))


if __name__ == "__main__":
    main()

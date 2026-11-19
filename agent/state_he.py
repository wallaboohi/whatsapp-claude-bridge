"""Owner 19.11.2026: one table in the bridge that translates every STATE (provider reasons, provider kinds, tunnel / failover /
backfill states, places, run results) to plain Hebrew with an icon. States repeat, so the bridge looks them up here instead of
showing a raw word. The raw command / description stays next to it (owner: "do not remove the raw commands and descriptions").
    he("quota") -> "🚫 מכסת השימוש נגמרה";  he("claude-remote") -> "🖥️ Claude בחשבון העבודה, דרך הלפטופ"
A state that is not in the table is returned as is (never hidden)."""
from emoji_icons import by_description

STATE_HE = {
    # provider problems (classify_down reasons)
    "quota": ("🚫", "מכסת השימוש נגמרה"), "rate": ("🚦", "הספק מגביל קצב או עמוס"), "billing": ("💳", "בעיית יתרה או חיוב אצל הספק"),
    "key": ("🔑", "המפתח נדחה"), "login": ("🔐", "ההתחברות פגה"), "unreachable": ("📡", "הספק לא מגיב או אין חיבור"),
    "rejected": ("🙅", "הספק דחה את פורמט הבקשה"), "error": ("⚠️", "שגיאה לא מוכרת"),
    # provider kinds
    "claude": ("🧠", "Claude במחשב הזה"), "claude-remote": ("🖥️", "Claude בחשבון העבודה, דרך הלפטופ"), "desktop-app": ("🪟", "אפליקציית שולחן עבודה"),
    "cli": ("⌨️", "כלי שורת פקודה"), "anthropic": ("☁️", "ממשק ענן"), "opencode": ("🧩", "OpenCode"), "auto": ("🎚️", "בחירה אוטומטית של מודל"),
    # places
    "remote": ("☁️", "ענן"), "local": ("🏠", "במחשב"),
    # tunnel / failover / backfill / agent states
    "up": ("🟢", "פועל"), "down": ("🔴", "נפל"), "off": ("⚫", "כבוי"), "idle": ("💤", "ממתין, אין עבודה"), "running": ("🏃", "רץ עכשיו"),
    "away": ("🚶", "המחשב מסור לגיבוי (מושבת בכוונה)"), "standby": ("🛋️", "גיבוי בהמתנה"), "grace": ("⏳", "תקופת המתנה לפני העברה"),
    "paused": ("⏸️", "מושהה"), "connected": ("🔗", "מחובר"), "disconnected": ("🔌", "מנותק"), "busy": ("🔒", "תפוס"),
    # run results
    "ok": ("✅", "הצליח"), "failed": ("❌", "נכשל"), "done": ("✅", "בוצע"), "not_done": ("🚫", "לא בוצע"), "pending": ("⏳", "ממתין"),
    "cancelled": ("🗑️", "בוטל"), "expired": ("⌛", "פג תוקף"), "draft": ("📝", "טיוטה"), "sent": ("📤", "נשלח"),
}


def he(state, icon=True):
    """Hebrew for a state word; unknown -> the word itself."""
    s = str(state or "").strip()
    e = STATE_HE.get(s) or STATE_HE.get(s.lower())
    if not e:
        return s
    return f"{e[0]} {e[1]}" if icon else e[1]


def icon_for(desc, used=()):
    """An icon picked from the legend by a Hebrew description (for stages not in the table)."""
    r = by_description(desc, used=used)
    return r[0] if r else ""

"""בדיקת תיאור קבוצת הזמנת האוכל — שלב 1 (קריאה בלבד, בלי שליחה ובלי עדכון קבוצה).

שימוש: uv run -q --no-project python -I -X utf8 lunch_check.py [--date YYYY-MM-DD]
פלט: lunch/out/<תאריך>.txt — הטקסט המתוקן + רשימת השינויים. לא נוגע בקבוצה ולא שולח כלום.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GROUP_JID = "120363429474123717@g.us"  # פרוייקט גדר מזרחית - מקטע D
OUT_DIR = os.path.join(HERE, "out")

# כללי שעות: ימים א'-ה' (ראשון = weekday 6 בפייתון)
def times_for(day: dt.date):
    if day.weekday() == 6:  # ראשון
        return "10:10", "11:30"
    return "09:10", "10:30"


def group_description():
    """קריאה בלבד: /api/group_info של הגשר (אותו מנגנון כמו tools/wa_admin.py)."""
    import urllib.request
    token = open(os.path.join(ROOT, "whatsapp-bridge", "store", "api_token"), encoding="utf-8").read().strip()
    req = urllib.request.Request(
        "http://127.0.0.1:8765/api/group_info",
        json.dumps({"jid": GROUP_JID}).encode("utf-8"),
        {"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    data = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
    return data.get("description") or data.get("topic") or ""


# תיקוני איות ידועים
SPELL = {"חומס": "חומוס", "ברגית": "פרגית", "קרוב לבן": "כרוב לבן"}  # owner 11.11: typos that went to Tzipora

# מילת מפתח -> אייקון נכון
ICON_FOR = {"בגט": "🥖", "פיתה": "🫓", "חביתה": "🍳", "שניצל": "🍗", "פרגית": "🍗"}
ALL_ICONS = set("🥖🫓🍳🍗🍖")


def fix_line(line, changes):
    """בודק כל מקטע (מופרד ב-' - ') בשורה: אייקון מול מילת המאכל שבו.
    מחליף אייקון שגוי באייקון הנכון; אם אין אייקון — מוסיף לפני המילה."""
    m = re.match(r"^(\s*\d+\s*-\s*\S+\s*-?\s*)(.*)$", line)  # קידומת: "1- שם -"
    head, rest = (m.group(1), m.group(2)) if m else ("", line)
    segs = re.split(r"(\s-\s|-\s)", rest)  # שומר את המפרידים
    new_segs = []
    for seg in segs:
        if seg.strip() in ("-", ""):
            new_segs.append(seg)
            continue
        food = next(((w, i) for w, i in ICON_FOR.items() if w in seg), None)
        if food:
            word, icon = food
            present = [c for c in seg if c in ALL_ICONS]
            if present and present[0] != icon:
                fixed = seg.replace(present[0], icon, 1)
                changes.append(f"אייקון: '{seg.strip()}' → '{fixed.strip()}'")
                seg = fixed
            elif not present:
                fixed = seg.replace(word, f"{icon} {word}", 1)
                changes.append(f"אייקון חסר: '{seg.strip()}' → '{fixed.strip()}'")
                seg = fixed
        new_segs.append(seg)
    return head + "".join(new_segs)


def check(text, day: dt.date):
    changes = []
    lines = text.split("\n")
    end_t, meal_t = times_for(day)
    out = []
    for line in lines:
        new = line
        for bad, good in SPELL.items():
            if bad in new:
                new = new.replace(bad, good)
                changes.append(f"איות: '{bad}' → '{good}'")
        if "מסיימים שינויים עד" in new:
            fixed = re.sub(r"\d{2}:\d{2}", end_t, new)
            if fixed != new:
                changes.append(f"שעת סיום שינויים → {end_t}")
            new = fixed
        if "מכוונים ל-" in new:
            fixed = re.sub(r"\d{2}:\d{2}", meal_t, new)
            if fixed != new:
                changes.append(f"שעת ארוחה → {meal_t}")
            new = fixed
        if "━━━" in new and re.search(r"\d{2}-\d{2}-\d{4}", new):
            fixed = re.sub(r"\d{2}-\d{2}-\d{4}", day.strftime("%d-%m-%Y"), new)
            if fixed != new:
                changes.append(f"תאריך → {day:%d-%m-%Y}")
            new = fixed
        if re.match(r"^\s*\d+\s*-", new):
            new = fix_line(new, changes)
        out.append(new)
    return "\n".join(out), changes


SCHEDULE_DB = os.path.join(ROOT, "whatsapp-bridge", "store", "messages.db")
ORDER_NAME = re.compile(r"^\s*\d+\s*-\s*(\S+)")


def ordered_names(text):
    """שמות מהזמנה: השורות הממוספרות (1- ג׳לאל - ...) — בלי קטע האמבולנס (לא בסידור 4M, לפי יקיר)."""
    names = []
    in_ambulance = False
    for line in text.split("\n"):
        if "אמבולנס" in line:
            in_ambulance = True
            continue
        if "צוות 4M" in line:
            in_ambulance = False
        m = ORDER_NAME.match(line)
        if m and not in_ambulance:
            names.append(m.group(1).strip("-,"))
    return names


def latest_schedule():
    """סידור העבודה האחרון שנשלח לקבוצה (קריאה בלבד מ-messages.db)."""
    import sqlite3
    c = sqlite3.connect(f"file:{SCHEDULE_DB}?mode=ro", uri=True)
    row = c.execute("select content from messages where content like '%סידור עבודה למחר%' order by timestamp desc limit 1").fetchone()
    c.close()
    return row[0] if row else ""


def cross_check(desc, schedule):
    """מי שהזמין ולא בסידור, ומי בסידור ולא הזמין (לבדיקה ידנית)."""
    tokens = set(re.split(r"[\s\-,/()+]+", schedule))
    ordered = ordered_names(desc)
    not_on_schedule = [n for n in ordered if n not in tokens]
    return not_on_schedule, []  # לפי יקיר: רק מי שהזמין ולא בסידור. מי שבסידור ולא הזמין — לא נבדק


def main():
    day = dt.date.today()
    if "--date" in sys.argv:
        day = dt.date.fromisoformat(sys.argv[sys.argv.index("--date") + 1])
    desc = group_description()
    if not desc:
        sys.exit("לא התקבל תיאור מהגשר")
    fixed, changes = check(desc, day)
    schedule = latest_schedule()
    not_on_sched, not_ordered = cross_check(desc, schedule)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{day.isoformat()}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(fixed)
        f.write("\n\n--- שינויים ---\n")
        f.write("\n".join(changes) if changes else "אין שינויים")
        f.write("\n\n--- הצלבה עם סידור העבודה ---\n")
        f.write("הזמין ולא מופיע בסידור: " + (", ".join(not_on_sched) or "אין") + "\n")
        f.write("\nלא נבדק עדיין: רשימת האנשים מול המצבת, ו'הזמנה מאתמול'.\n")
    with open(os.path.join(OUT_DIR, f"{day.isoformat()}_raw.txt"), "w", encoding="utf-8") as f:
        f.write(desc)  # תמונת מצב של התיאור הנוכחי, להשוואה למחר
    print(path)
    print(f"{len(changes)} שינויים")
    for c in changes:
        print("-", c)
    print("הזמין ולא בסידור:", ", ".join(not_on_sched) or "אין")


if __name__ == "__main__":
    main()

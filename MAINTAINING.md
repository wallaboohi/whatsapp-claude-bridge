# תחזוקת הכלי (למתחזק) · Maintaining

## איך גרסה מגיעה למשתמשים
| רכיב | איך המשתמש מקבל עדכון |
|---|---|
| הגשר, הסוכן, הכלים של Claude (MCP) | כל בוקר הסוכן בודק אם יש גרסה חדשה ושולח הודעה בקבוצת Claude. המשתמש לוחץ בלוח הבקרה **"בדוק עדכון לכל הכלי" ← "עדכן עכשיו"**, או מריץ `update.cmd`. |
| לוח הבקרה (WhatsAppDash.exe) | מתעדכן יחד עם הכלי, או לבד בכפתור "בדוק עדכון ללוח הבקרה". |
| התוסף ב-Claude Code (סקיל + הגדרת MCP) | `claude plugin update whatsapp-bridge@wallaboohi`, או עדכון אוטומטי אם המשתמש הפעיל אותו. |

**העדכון בטוח:** הוא עוצר את הגשר והסוכן, עובר לגרסה החדשה, מוריד גשר חדש, מעדכן רכיבים, מפעיל, ומריץ בדיקה עצמית.
אם משהו נכשל — חוזר אוטומטית לגרסה הקודמת. הגדרות ונתונים אישיים לא נוגעים בהם (הם מחוץ לגיט).

## שחרור גרסה חדשה
1. מתקנים ובודקים (Claude עושה את זה בשבילך) ושומרים בגיט (commit).
2. בדיקה בלי לפרסם:
   ```bat
   release.cmd 1.1.0 "מה השתנה" --dry-run
   ```
3. פרסום:
   ```bat
   release.cmd 1.1.0 "מה השתנה"
   ```
   הפקודה מעדכנת את מספר הגרסה בכל הרכיבים, כותבת ביומן השינויים, מריצה את כל הבדיקות
   (בדיקות הגשר, בדיקות הסוכן, כלי ה-MCP, בדיקת התוסף, **סריקה שאין מידע אישי**), שומרת, מתייגת ודוחפת.
   GitHub Actions בונה את הגשר ואת לוח הבקרה ומפרסם את הגרסה — ומשתמשים יקבלו הודעה בבוקר שאחרי.
4. לבדוק שה-build עבר: https://github.com/WallaBoohi/whatsapp-claude-bridge/actions

**מספרי גרסה:** `1.0.x` תיקון באג · `1.x.0` יכולת חדשה · `x.0.0` שינוי שמחייב פעולה מהמשתמשים (לכתוב בהערות מה לעשות).

## תקלות ודיווחים מהקהילה
- דיווחים מגיעים כ-Issues במאגר. בקש מ-Claude: "תעבור על ה-Issues הפתוחים ותציע תיקונים".
- לפני כל שחרור: `uv run python -X utf8 tools/selftest.py` על המחשב שלך (21 בדיקות על חשבון אמיתי).
- אם גרסה שוחררה עם באג: משחררים מיד גרסת תיקון (`1.1.1`). משתמש שהעדכון נכשל אצלו חוזר אוטומטית לגרסה הקודמת.

## גיבוי אוטומציות בין מחשבים (failover)
מחשב גיבוי מריץ תוספים של מחשב אחר כשהוא כבוי, רק אחרי אישור הבעלים במשפט המדויק (ברירת מחדל "מאשר לנייח"), ומחזיר אותם כשהוא חוזר. פירוט: ARCHITECTURE.md §3.
- הפעלה: במחשב הגיבוי בלבד `"failover": {"for": ..., "plugins": [...], ...}` + הפעלה מחדש. דרוש: ssh מהגיבוי למחשב השני (peer `ssh`/`tunnel`),
  venv של הסוכן שם (`agent/.venv/Scripts/python.exe`), ו-ssh הפוך מהמחשב עם התוספים לגיבוי (בדיקת ההחזקה).
- תקלה: `agent/failover/hold-*.json` במחשב הגיבוי = הוא מחזיק; kv `failover:<שם>` = המצב. ביטול ידני: לעצור את הסוכן בגיבוי, למחוק את קובץ ההחזקה ו-`failover:<שם>`.
- בדיקות: `test_failover_*` ב-`agent/tests/test_agent.py` מריצות את הסקריפטים ה"מרוחקים" מקומית.

## English summary
Users update from the dashboard ("Check for a tool update") or `update.cmd` (self-tested, automatic rollback); the agent
announces new releases in the owner's WhatsApp control group daily. Maintainers release with
`release.cmd X.Y.Z "notes"` (bumps versions, runs all checks incl. a personal-data scan, tags and pushes; GitHub Actions
builds and publishes `whatsapp-bridge.exe`, `WhatsAppDash-X.Y.Z.exe` and `latest.json`).

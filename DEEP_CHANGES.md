# הוראות לבינה שמבצעת שינויי עומק בגשר (יקיר, 14.11.2026)

קרא את הקובץ הזה במלואו לפני שאתה נוגע בקוד. כל כלל כאן מגיע ממקור שמצוין לידו. אם משהו לא ברור: עצור ושאל את יקיר. אל תשלים מהדמיון.

## 0. נקודת הגיבוי (נוצרה 14.11.2026 13:40)
- **בגיט:** התג `backup-2026-11-14-pre-deep-change` מצביע על הקומיט `72cd2a2`, ונשמר ב-hub. באותו רגע שני המחשבים (נייח YB-PC-HW ולפטופ 4-MINE) היו על `72cd2a2` = `hub/main`. השחרור האחרון שפורסם: `v1.1.2`.
- **קבצי גיבוי בכל מחשב:** `C:\Users\walla\WhatsApp-Backup\code\2026-11-14_1340_pre-deep-change\`
  - `whatsapp-mcp-all.bundle`: כל המאגר, כל הענפים והתגים (`git bundle verify` עבר בשני המחשבים).
  - `personal-config-home-secrets.zip`: מה שלא נמצא בגיט: `agent/*.json` (הגדרות, ספקים, קבוצות), `agent/home` (כללים, מילון, handoff), `secrets/`, `whatsapp-bridge/store/*.json` (מדיניות שליחה). המסדים (`messages.db`, `agent.db`) מגובים בגיבוי הלילי הרגיל.
- **חזרה לנקודה:** `git -C C:/Users/walla/whatsapp-mcp checkout main && git reset --hard backup-2026-11-14-pre-deep-change`. זו פעולה הרסנית: רק באישור מפורש של יקיר. אם המאגר נפגע: `git clone <bundle> whatsapp-mcp-restore`.

## 1. ⚠️ השחרור הלילי: הסכנה הכי גדולה בשינוי עומק
- **המקור:** `agent/agent.py` → `auto_release()`. בכל לילה ב-23:30, המחשב שמוגדר `auto_release: true` ב-`agent/config.json` מפרסם ל-GitHub כל מה שיש ב-`hub/main` כגרסת תיקון הבאה (`tools/release.py`: בדיקות, סריקה, תג, דחיפה). ב-23:50 העדכון האוטומטי מתקין אותה בכל המחשבים.
- **המשמעות:** שינוי עומק שנדחף ל-`main` באמצע העבודה יפורסם ויותקן הלילה, גם אם הוא לא גמור.
- **הכלל:** שינוי עומק מתבצע **בענף נפרד** (למשל `deep/<נושא>`), שנדחף ל-hub: `git push hub deep/<נושא>`. מיזוג ל-`main` רק אחרי שהכול עובד ונבדק, ורק באישור יקיר. `auto_release` משחרר רק את `main` (הוא בודק את `HEAD` של המחשב שמשחרר, והמחשב הזה חייב להישאר על `main`).
- לא מכבים את `auto_release` בלי אישור יקיר.

## 2. גרסאות: אסור לקבוע מספר בעצמך
- **המקור:** `tools/versioning.py`, `AGENTS.md`, `AI_HOSTING.md` §8.
- **החוק:** מספר אחד לכל שחרור, תיקון (patch) אחד בכל פעם: 1.1.2 ← 1.1.3 ← 1.1.4. לא מדלגים, לא חוזרים אחורה, ולא משתמשים שוב במספר שכבר קיבל תג (גם אם הבנייה שלו נכשלה). אפליקציית הלוח נושאת תמיד את אותו מספר של הכלי.
- **הפקודות:** `uv run --no-project python tools/versioning.py current` (עכשיו `1.1.2`), `next` (עכשיו `1.1.3`), `check X.Y.Z`.
- **אל תערוך** `VERSION`, `dash/VERSION`, `plugin.json`, `pyproject.toml` ידנית. שחרור נעשה רק דרך `tools/release.py`, שבודק את החוק לפני שהוא נוגע בקובץ גרסה.
- **חריגה** (מספר משני או ראשי): רק באישור של יקיר בווצאפ, `/גרסה אשר X.Y.Z`. בינה לא יכולה לאשר לעצמה.
- **סתירה בין מקורות:** ב-`MAINTAINING.md` (שורה 28) כתוב "`1.x.0` יכולת חדשה". זה כלל ישן. החוק של יקיר מ-14.11 (`tools/versioning.py`) גובר: תיקון אחד בכל שחרור.
- שחרור ידני ל-GitHub רק כשיקיר כותב "שחרר" (`agent/home/CLAUDE.md`, "פרסום ל-GitHub").

## 3. סנכרון בין שני המחשבים
- **המקור:** `agent/home/CLAUDE.md`, "פיתוח הכלי — מאגר קוד משותף".
- מקור האמת: ה-remote `hub` (`C:/Users/walla/whatsapp-mcp-hub.git` בלפטופ; בנייח דרך ssh `4-MINE`).
- לפני כל שינוי: `git -C C:/Users/walla/whatsapp-mcp pull --ff-only`. אם זה לא fast-forward: עצור ושאל את יקיר. לא ממזגים בכוח.
- אחרי כל קומיט: `git push hub <ענף>`. ב-`main`: `git push hub HEAD:main`, ואז `git pull --ff-only` גם במחשב השני.
- לא בונים את אותו פיצ'ר בשני המחשבים: לפני שמתחילים, `git log hub/main -10` ו-`TODO.md`.
- `git add` רק לקבצים ששינית. לא `git add .` ולא תיקיות שלמות (`AGENTS.md`).

## 4. בדיקות: לפני ואחרי כל שינוי
- `cd agent && uv run -q python -X utf8 -m pytest tests/ -q`. ב-14.11 13:40: **162 עוברות, 7 מדולגות.** מספר נמוך יותר אחרי שינוי = נסיגה.
- בדיקות אמיתיות מול ספקים (צורכות מכסה): `LIVE_ENGINE=1 uv run -q python -X utf8 -m pytest tests/test_engine_live.py -q`.
- **שינוי בלוח:** נבדק בחלון שיקיר רואה באמת. האפליקציה `WhatsAppDash.exe` מריצה עותק קפוא משלה (הגרסה שבה נבנתה); הלוח החי הוא `http://127.0.0.1:8770` (קיצור "לוח בקרה חי" בשולחן העבודה). מצלמים לפני שאומרים "עובד".
- מדווחים בכנות: מה נבדק, מה לא נבדק, ומה נכשל.

## 5. מה לא עושים בלי אישור מפורש של יקיר
- מחיקה (מעבירים לסל מיחזור בתיקייה), `git reset --hard`, `push --force`, הזזת תג.
- הפעלה מחדש של הגשר, הסוכן או הלוח בזמן שיקיר עובד בו (מנגנון בטוח: הקובץ `agent/restart.request`, שמפעיל מחדש אחרי התשובה הנוכחית).
- שליחה לאחרים (רק טיוטות דרך `tools/wa_send.py`), פרסום ל-GitHub, שינוי של `auto_release`.
- סודות נשארים ב-`secrets/` ולא מודפסים.

## 6. מפת הקוד (כדי לא לנחש)
- `ARCHITECTURE.md`: כל הרכיבים. `AI_HOSTING.md`: ספקים, מנועים, גיבוי (סעיפים 0 עד 14). `MAINTAINING.md`: עדכון ושחרור.
- הגשר (Go): `whatsapp-bridge/`. הסוכן: `agent/agent.py`. הלוח: `dash/server.py` + `dash/static/`. כלים: `tools/`.
- נתונים אישיים מחוץ לגיט: `agent/config.json`, `agent/groups.json`, `agent/provider.json`, `agent/home/`, `secrets/`, `whatsapp-bridge/store/`.

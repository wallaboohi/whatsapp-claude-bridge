# Changelog

## 1.0.11 — 2026-11-08
- Dashboard: one ⚙️ Settings window with 9 topic tabs (general, voice, models, cost, groups, sending, schedules, computers, plugins/git), a help line for every se
- Voices: Thalita default (multilingual voices speak Hebrew), 8 voices in the dashboard + voice style normal/soft
- Nightly auto-release (auto_release_time 23:30, maintainer machine only): everything new in the shared repo becomes the next patch version; daytime changes go on
- 😎 = voice note reading the bot's last answer (message) or the reacted message (reaction); bridge publishes the owner's reactions
- Group names follow the model in every connected group (גשר אופוס ווצאפ): on model change in WhatsApp/dashboard and per run in auto; toggle group_name_model
- agent: daily sync before the midnight session change (day_sync_time 23:40): save the day's tracked changes if tests pass, pull/push hub, rebuild the bridge and 
- Voices: Hila (female) is the default; dashboard voice selector with names + ▶ preview; agent.py tts CLI
- Status icons: ⬛ command (cmd), 🌐 network git vs 🗂️ local git, robot flips 🤖/🦾 with the hourglass; cost cap message: 🔒 + how to bypass (!, !!!, dashboard)
- agent: bridge health alerts (Windows notification here, reason in the other machine's alert), stage reactions on the owner's message (✅/❌ at the end), /מחכים + 
- Per-conversation time limit (groups.json timeout_minutes / claude_timeout_minutes, editable) with a clear 'stopped after N minutes' reply; dashboard accepts htt
- bridge: connection health (keep-alive timeout -> forced reconnect, outdated client, connect failures, stream errors) in /api/status + conn_log; delivery/read re
- Status message: hourglass flips ⏳/⌛ on every edit, edited every 5 s (status_edit_seconds)
- Peer watch alerts, mutual nightly backup, start agent on the other machine, transcription backfill after off-time, cost per machine/account, on-demand history s

## 1.0.10 — 2026-11-08
- עדכון אוטומטי כל לילה ב-23:50: הודעה, עדכון, הפעלה מחדש של הגשר והסוכן, ובדיקה — חוזר לגרסה הקודמת אם משהו נכשל
- לוח בקרה לכמה מחשבים: שיחות המחשב השני כלשוניות, כרטיס "מחשבים" עם עצירה/הפעלה לכל מחשב (חיבור SSH שהסוכן שומר)
- שליטה מלאה מהלוח: השהיית האזנה, תור פקודות (ביטול/ניסיון חוזר), טיוטות מתוזמנות ותזכורות, עצירה והפעלה אמיתית של גשר וסוכן, כל ההגדרות והתזמונים, תוספים
- סוגי הודעות נוספים בגשר: אירוע, מיקום, תיוג — וקליטה של כל סוגי ההודעות הנכנסות
- מצב הרשאות של Claude ניתן להגדרה (permission_mode)
- edge-tts נכלל בהתקנה (תשובה קולית לא תיעלם בעדכון)
- (1.0.9 לא נבנתה — בדיקה לא יציבה ב-GitHub; תוקנה)

## 1.0.9 — 2026-11-08
- עדכון אוטומטי כל לילה ב-23:50: הודעה, עדכון, הפעלה מחדש של הגשר והסוכן, ובדיקה — חוזר לגרסה הקודמת אם משהו נכשל
- לוח בקרה לכמה מחשבים: שיחות המחשב השני כלשוניות, כרטיס "מחשבים" עם עצירה/הפעלה לכל מחשב (חיבור SSH שהסוכן שומר)
- שליטה מלאה מהלוח: השהיית האזנה, תור פקודות (ביטול/ניסיון חוזר), טיוטות מתוזמנות ותזכורות, עצירה והפעלה אמיתית של גשר וסוכן, כל ההגדרות והתזמונים, תוספים
- סוגי הודעות נוספים בגשר: אירוע, מיקום, תיוג — וקליטה של כל סוגי ההודעות הנכנסות
- מצב הרשאות של Claude ניתן להגדרה (permission_mode)
- edge-tts נכלל בהתקנה (תשובה קולית לא תיעלם בעדכון)

## 1.0.8 — 2026-11-08
- מכסת שימוש של Claude: הודעה "⏳ אמשיך לבד ב-HH:MM" והמשך אוטומטי של הפקודה כשהמכסה מתאפסת
- התראה כמה ימים לפני שההתחברות של Claude במחשב פגה
- חזרת חיבור: למה נותק, כמה זמן, והודעות שלא נקלטו בזמן הניתוק מטופלות
- בדיקה עצמית כל לילה — הודעה רק אם משהו נכשל
- שליחה מתוזמנת: תשובה לכרטיס טיוטה עם שעה (08:00) שולחת אותה בזמן
- סיכום בוקר: מה קרה בשיחות בלילה ומי מחכה לתשובה
- תשובה קולית כשמקליטים לסוכן
- כל קבוצה עובדת במקביל; פקודה שנקטעה לא רצה שוב לבד; התראה כשהודעה לא נקלטה; המודל קבוע לאורך סשן; תקרה כוללת לכל הקבוצות
- הגשר: עולה מחדש אחרי ניתוק, מגבלה יומית שלא מתאפסת, סקר ואיש קשר אמיתיים בטיוטה, עריכות ומחיקות נשמרות, השהיה רק על חסימה אמיתית
- תחזוקה: הגשר פוצל לקבצים קטנים + בדיקות לכל ה-API

## 1.0.7 — 2026-11-08
- כמה קבוצות בקרה: לכל קבוצה סשן, הוראות, מודל ותקרת עלות משלה; "➕ חבר קבוצה" מהלוח
- כרטיסיות שיחה בבר בולט מתחת לכותרת: קבוצה לכל כרטיסייה + "שיחה מקומית" בלי ווצאפ
- "שלח גם לווצאפ" לכל קבוצה, חיווי במחשב/בנייד, והמשך השיחה מהנייד מאותה נקודה
- חיפוש בכל ההיסטוריה (🔍 / Ctrl+K), סובל שגיאות כתיב, פותח את ההודעה בהקשר
- צירוף והדבקת קבצים בשיחה, כפתור ⛔ עצור, "הצג עוד" להודעות ארוכות

## 1.0.6 — 2026-11-07
- לחצן ימני בשיחה: העתק, גזור, הדבק, בחר הכול, העתק הודעה, העבר לשורת הכתיבה
- הודעת המצב של Claude מוצגת כבועה אחת בתוך השיחה, עם רשימת השלבים
- השיחה ממלאת את כל החלון ותיבת הכתיבה לא נחתכת; בחירת המודל בעמודה הצדדית

## 1.0.5 — 2026-11-07
- לוח בקרה: כתב השיחה ותיבת ההקלדה גדולים יותר
- כרטיס "תצוגה": גודל כתב השיחה, גודל הממשק, גופן ורקע שיחה (נשמר במחשב)
- כפתור "הפעל הכול מחדש" (גשר + סוכן)
- אנטר מעביר את ההודעה מיד לחלון השיחה, בלי שליחה כפולה; המתנה עד 60 שניות לגשר
- בחירת המודל עברה לתוך חלון השיחה, עם מסגרת שפועמת בזמן עבודה
- חלונות שלבים: 8 הפעולות האחרונות של Claude (קורא / עורך / מריץ / גיט…)
- בווצאפ: הודעת המצב לא נמחקת — נערכת לפי הפעולה ונשארת בסוף כ"✅ סיימתי"

## 1.0.4 — 2026-11-07
- תיקון חישוב העלות בסשן רציף: נשמרת עלות כל ריצה בלבד (קודם נספרה עלות מצטברת והתקרה היומית חסמה מוקדם מדי)
- כרטיס טיוטה ברור: מספר טיוטה, ושורה ראשונה "לחיצה ארוכה ← ✅ או 👍"
- /הפעל מחדש בקבוצה: הפעלה מחדש מסודרת של הסוכן ודיווח "עליתי מחדש" עם ההגדרות
- הסוכן מחכה לגשר בעלייה ושולח שוב הודעה שנכשלה כי הגשר לא עלה
- הודעת "החיבור חזר" כוללת סיבה משוערת
- העברת הודעות גם לתמונות, וידאו, הקלטות וקבצים
- בדיקות חדשות (אישור חד-פעמי, השהיה, כרטיס, עלות) · הערות גרסה ב-GitHub רק לגרסה הנוכחית

## 1.0.3 — 2026-11-07
- לוח בקרה: מגירת "נשלחו" עם טקסט ההודעה וסינון "לאנשים אחרים"
- מגירת "עלות": ניצול התקרה היומית ופילוח לפי מודל (נרשם מודל לכל ריצה)
- מגירת "התקבלו": קיבוץ לפי שיחה, חיפוש וסינון לפי שיחה, שמות אנשי קשר

## 1.0.2 — 2026-11-07
- אישור טיוטה בתגובת ✅ / ביטול ב-❌ על כרטיס הטיוטה
- יכולות גשר חדשות: שם/תיאור/תמונה/הגדרות קבוצה, משתתפים, קישור הזמנה, עריכה ומחיקת הודעות, חסימה, אודות, סטטוס, הצמדה, כוכב, סימון כנקרא, סקרים, איש קשר, העברה
- הגנה מעברית פגומה ("????") בשמות/תיאורים + tools/wa_admin.py שקורא מקובץ UTF-8
- תזכורות (tools/wa_remind.py), OCR אופליין (Tesseract עברית+אנגלית, או OCR של ווינדוס)
- בחירת מודל: /מודל בווצאפ + רשימה נפתחת בלוח עם הסברים; המלצה מתי להחליף; !! = המודל החזק להודעה אחת
- הודעת סטטוס אחת שנערכת בזמן שהסוכן עובד; סשן רציף עם סיכום בהחלפה
- לוח בקרה מעוצב מחדש: כותרת עם מצב וגרסה, סרגל חיבור לרוחב, שיחה עם תיבת המשך, פעילות היום, מגירת טיוטות עם אישור/מחיקה, כרטיס קבוצת הבקרה (שינוי שם / יצירת קבוצה ייעודית), שמירה בגיט ושחרור (מתחזק)
- הוסר: מצב שליחה עצמאית לזמן מוקצב, קוד טיוטה בטלפון (ADB)

## 1.0.1 — 2026-11-06
תשובות בכתב נטוי עם ציטוט ההודעה שלך · !!! מבטל את תקרת העלות לשעה · Claude זול יותר (Haiku לשאלות קצרות, בלי טעינת כלים מיותרים) · תקרה יומית ברירת מחדל 20$ / Italic replies quoting your message · !!! lifts the cost cap for an hour · cheaper Claude runs · 20 USD default cap

## 1.0.0 — 2026-11-06
First release of the enhanced fork.

**Bridge**
- REST API protected by a bearer token; push events `/api/events`; `/api/status`, `/api/policy`, `/api/react`, `/api/typing`, `/api/create_group`, `/api/leave_group`.
- Setup creates the private "Claude" control group automatically (no manual group creation).
- Outgoing messages stored (`send_log`); media downloaded on arrival with unique file names; fixed upstream 403 on media download (URL tokens kept).
- Polls and decrypted votes; edits and deletions; LID-addressed chats stored under the phone number; WAL databases; connection log.
- Send policy: drafts-only by default, time-limited auto mode, health guard (pause on throttling / temporary ban), human-like pacing, quoted replies.

**Agent**
- Chat with Claude from a WhatsApp control group: 👀/✅ reactions, progress note, files in and out, durable command queue, daily session rotation with hand-off, daily budget cap, light model for short questions.
- Local voice transcription (optional extra), Windows notifications on disconnect, nightly backup with retention, log rotation, plugins, Hebrew/English.

**Dashboard** — `WhatsAppDash.exe` (offline, tray icon, light/dark, Hebrew/English, self-update).

**MCP server** — `get_status`, `search_all`, `list_transcripts`, `list_polls`, `list_drafts`, `react_to_message`, quoted replies in `send_message`.

**Tooling** — `setup.cmd` wizard, `tools/selftest.py` (21 checks), Go and Python unit tests, GitHub Actions build and release.

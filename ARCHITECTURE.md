# ארכיטקטורה — WhatsApp ↔ Claude

מסמך מפה מלא של הכלי: מה הרכיבים, איך הם מדברים, איפה כל נתון נשמר, ואיך מתחזקים. נועד לכך שכל סשן חדש (או מתחזק חדש)
יבין את המערכת בלי חורי מידע. פרטים אישיים של התקנה מסוימת — לא כאן (ראה "קבצים אישיים" למטה).

```
 📱 הנייד (ווצאפ)                🌐 שרתי ווצאפ                    🖥️ המחשב (Windows)
 ┌─────────────────┐          ┌──────────────┐     ┌─────────────────────────────────────────────────────────┐
 │ קבוצת בקרה פרטית │◀────────▶│  WhatsApp     │◀───▶│ whatsapp-bridge.exe (Go, whatsmeow) — "מכשיר מקושר"       │
 │ (רק הבעלים)      │          │  multi-device │     │   REST + SSE על 127.0.0.1:8765 (טוקן)  ·  store/*.db      │
 └─────────────────┘          └──────────────┘     │        ▲                      ▲                ▲          │
                                                    │        │ /api/*               │ /api/events    │ קריאה    │
                                                    │ agent.py (Python) ── claude -p (Claude Code, headless)    │
                                                    │   תמלול · פקודות · סשן · טיוטות · תזכורות · גיבוי · לוח 8770│
                                                    │ WhatsAppDash.exe (pywebview) — לוח בקרה שולחני           │
                                                    │ whatsapp-mcp-server (MCP) — כלים ל-Claude Code אינטראקטיבי │
                                                    └─────────────────────────────────────────────────────────┘
```

## 1. רכיבים

| רכיב | שפה / קובץ | תפקיד | איך רץ |
|---|---|---|---|
| **הגשר** | Go · `whatsapp-bridge/` (whatsmeow) | חשבון ווצאפ מקושר. שומר כל הודעה ב-SQLite, מוריד מדיה, חושף REST, אוכף מדיניות שליחה (טיוטות) | `run-bridge.cmd` (לולאה: עולה מחדש 10 שנ' אחרי יציאה), משימת Task Scheduler "WhatsApp bridge" בכניסה |
| **הסוכן** | Python · `agent/agent.py` | מאזין לקבוצת הבקרה, מריץ Claude Code על כל הודעה, מתמלל קוליות, תזכורות, גיבוי, מארח את הלוח על 8770 | `run-agent.cmd` (לולאה, 15 שנ'), משימה "WhatsApp agent" |
| **לוח הבקרה** | `dash/` — `server.py` (HTTP) + `static/` (HTML/JS/CSS) | מצב, שיחה, טיוטות, עלות, מודל, תצוגה, עדכונים/שחרור | בתוך הסוכן (http://127.0.0.1:8770) **וגם** כאפליקציה `WhatsAppDash.exe` (pywebview, פורט חופשי, קבצי static ארוזים בתוך ה-exe) |
| **שרת MCP** | Python · `whatsapp-mcp-server/` | כלים ל-Claude Code אינטראקטיבי: חיפוש, קריאה, שליחה (דרך הגשר), תמלולים, סקרים, טיוטות | נטען ע"י תוסף Claude Code (`.claude-plugin/`, סקיל `skills/whatsapp`) |
| **כלים** | `tools/` | `wa_send.py` (שליחה + יומן), `wa_admin.py` (קריאה לכל endpoint מקובץ UTF-8), `wa_remind.py` (תזכורות), `wa_push.py` (התראת פוש לטלפון), `ocr.ps1` (Tesseract עב+אנג / OCR ווינדוס), `selftest.py` (21 בדיקות חיות), `setup.py` (אשף התקנה), `update.py` (עדכון עם חזרה), `release.py` (שחרור), `migrate_lid.py` | ידני / ע"י Claude |

## 2. הגשר (Go)

**קבצים:** `main.go` (רק main), `events.go` (מטפל אירועים, handleMessage), `store.go` (MessageStore), `send.go` (sendCore, מדיה),
`media.go` (הורדה), `history.go` (סנכרון היסטוריה), `http_api.go` (/api/send, /api/download, רישום כל ה-API), `enhanced.go` (סכמה + הגירות, טיוטות, קצב),
`policy.go` (מדיניות שליחה + כרטיס טיוטה, זיהוי חסימה), `approvals.go` (✅/❌ על כרטיס), `api_extra.go` (status, policy, create/leave_group, group_info, set_group, events SSE),
`group_admin.go` (ניהול קבוצות, עריכה/מחיקה, חסימה), `extras2.go` (אודות, סטטוס, הצמדה, כוכב, נקרא, סקר, איש קשר, העברה כולל מדיה),
`sync_extras.go` (react, typing, quoted_id), `gate.go` (owner_token — סיסמה נוספת, אותן הרשאות), `quote_phone.go`.

**Endpoints** (כולם POST JSON, `Authorization: Bearer <store/api_token>`, רק 127.0.0.1):
`send` `download` `react` `typing` `status` `policy` `events`(SSE) `draft_decide` · קבוצות: `create_group` `leave_group` `join_group` `list_groups`
`group_info` `set_group` `group_participants` `group_photo` `group_settings` `group_invite` · הודעות: `edit_message` `delete_message` `forward`
`pin_message` `star` `mark_read` `pin_chat` · שליחה מיוחדת: `send_poll` `send_contact` `post_status` `set_about` · `block`.

**מדיניות שליחה** (`store/send_policy.json`, נקרא בכל שליחה):
- `owner` (מספרים/LID של הבעלים) וקבוצת הבקרה → נשלח ישירות.
- כל נמען אחר → **טיוטה**: שורה בטבלת `drafts` + כרטיס בקבוצת הבקרה ("📝 טיוטה #N → שם", "לחיצה ארוכה ← ✅ או 👍", קישור wa.me).
- תגובת ✅/✔️/👍 **של הבעלים** על הכרטיס → `decideDraft` מסמן `sending` אטומית ושולח **הודעה אחת** (`sendCore(..., approved=true)`); ❌ מבטל.
  טיוטת סקר/איש קשר/העברה שומרת `kind`+`payload` ושולחת את הדבר האמיתי.
- `allow` = נמען שיקיר אישר במילים ("מאשר לך שליחה", כלל 4 ב-CLAUDE.md — Claude מוסיף, שולח אחת ומנקה). אין מצב auto (הגשר מתעלם ממנו).
- `paused_until` עתידי גובר על הכול (גם על אישור).
- קצב (`sendPacer`), מגבלה יומית (נספרת מ-`send_log`, לא מתאפסת בהפעלה מחדש; שיחות הבעלים וקבוצות הבקרה פטורות), עצירה אוטומטית בחשד לחסימה (`pauseSends`).
- `LoggedOut` → יציאה (הלולאה עולה ל-QR); `StreamReplaced` → יציאה אחרי 2 דק'.
- הגנת טקסט: `textOK` מסרב ל-"??" / U+FFFD (עברית שנשברה בקונסולה).

**בנייה:** `CGO_ENABLED=1` (sqlite3) עם gcc של msys64/ucrt64; `go build -ldflags "-X main.version=$(cat VERSION)"`.
החלפת exe בזמן ריצה: לבנות ל-`whatsapp-bridge-new.exe` → לעצור את התהליך → `whatsapp-bridge.exe`→`-prev.exe` → new→exe; הלולאה מעלה תוך 10 שנ'.

## 3. הסוכן (agent.py)

**חוטים:** `worker` (תמלול/טיפול בהודעות נכנסות + תוספים), `command_worker` (תור פקודות מהבעלים → Claude), `event_listener` (SSE מהגשר),
`scheduler` (גיבוי, בדיקת עדכון, בדיקת שם קבוצה, דוחות תוספים, תזכורות כל דקה), `watchdog` (ניתוק > 10 דק' → התראת Windows; חזרה → הודעה עם סיבה),
`sweep_loop` (גיבוי מנגנון אם אירוע פוספס), ושרת הלוח על 8770.

**זרימת הודעה מהבעלים:**
1. הודעה בקבוצת הבקרה (`claude_group`) → `enqueue_command` (טבלת `commands`, 👀).
2. `owner_command` — פקודות מקומיות בלי Claude: `/מודל …`, `/סשן`, `/סשן חדש`, `/הפעל מחדש`.
3. תקרה יומית (`daily_budget_usd`): `!` עוקף להודעה, `!!!` לשעה. `!!` = המודל החזק להודעה אחת.
4. `pick_model`: עקיפה (`model_override.json` מ-/מודל או מהלוח) → אחרת אוטו: `light_model`/`claude_model` (Sonnet), `deep_model` (Opus) למילות תכנון.
5. `claude()` → `claude -p --output-format stream-json [--resume <session>] [--model] [--strict-mcp-config]` בתיקייה `agent/home`
   (שם `CLAUDE.md` = ההוראות הקבועות, `handoff.md` = סיכום הסשן הקודם, `outbox/` = קבצים חוזרים).
6. `StatusMsg`: אחרי 8 שנ' הודעת "⏳ שלב · פרט · זמן · מודל" שנערכת כל 10 שנ'; בסוף נשארת "✅ סיימתי · זמן · מודל · N שלבים".
   השלבים נשמרים ב-kv `live_steps` (הלוח מציג אותם בבועה).
7. תשובה → `reply` (🤖 + נטוי + ציטוט), קבצי outbox נשלחים, ✅ על ההודעה, המלצת מודל 💡 אם רלוונטי.
8. `restart.request` קיים / `/הפעל מחדש` → יציאה נקייה אחרי התשובה; הלולאה מעלה; "✅ עליתי מחדש" עם ההגדרות.

**עוד בסוכן (08.11):** עובד לכל שיחה במקביל; פקודה שנקטעה → `interrupted` + הודעה; מכסת שימוש → `waiting_limit` והמשך אוטומטי בזמן האיפוס;
התראת פקיעת התחברות (`signin_check_time`); דוח חזרת חיבור (סיבה, משך, הודעות שלא נקלטו מטופלות); בדיקה עצמית לילית (`selftest_time`, מדווח רק כשל);
שליחה מתוזמנת (תשובה לכרטיס עם שעה → `scheduled_drafts` → draft_decide); סיכום בוקר (`morning_summary_time`); תשובה קולית להקלטה (edge-tts → ogg);
מודל דביק לסשן; תקרה כוללת `total_daily_budget_usd`.
עדכון אוטומטי לילי (`auto_update_time` 23:50): אם יש גרסה חדשה — הודעה, `tools/update.py` מנותק (עוצר, מעדכן, מפעיל מחדש גשר+סוכן, בדיקה, חזרה אחורה בכשל); הסוכן החדש מדווח את התוצאה. במחשב מפתח (ענף) — fast-forward בלבד.
**סשן:** `fresh_sessions:false` = רציף (`kv session_id`), מתחלף בחצות או אחרי `session_max_runs` (60) בעבודה שאינה פיתוח (`is_dev_work`), עם `write_handoff`.
**עלות:** `claude_runs.cost` = עלות הריצה בלבד; `cum` = המצטבר ש-Claude מדווח בסשן רציף (`run_cost`).
**התראות פוש (09.11):** הגשר מדבר בשם הבעלים, ולכן הודעות שלו לא מצלצלות. אירוע ווצאפ כן: הטלפון מתריע בשעת ההתחלה. `push()` → `push_queue` (agent.db) → `push_loop` שולח אירוע שמתחיל 5 שנ' אחר כך, בקבוצה של השיחה, אחרי שהתשובה יצאה; התראות בתוך 20 שנ' מאוחדות; שעות שקט `push_quiet` (23–07, חוץ מ-fault). מקורות: `tools/wa_push.py` (Claude בכל שיחה), סיום משימה מעל `push_long_minutes`, תקלות, אנשים מחכים `push_wait_hours` + שיחה שלא נענתה מ-`push_important` (פעם אחת לכל חשבון ווצאפ; מחשב שותף לוקח כשהמוביל לא זמין, `/brief.account`), תזכורת בזמנה, מגבלת שימוש 80%. לא על טיוטות. כיבוי: `push_alerts:false`.
**תוספים:** `config.plugins` — קבצי Python עם hooks (למשל קליטת טפסים), נטענים ב-`load_plugins`.

## 4. נתונים

| איפה | מה |
|---|---|
| `whatsapp-bridge/store/messages.db` | `messages` (כל ההודעות, edited/deleted), `chats` (שמות), `drafts` (+card_id, quoted_id), `send_log`, `polls`, `poll_votes`, `conn_log` (זמני חיבור לסרגל) |
| `whatsapp-bridge/store/whatsapp.db` | מצב whatsmeow: מכשיר, מפתחות, `whatsmeow_contacts`, `whatsmeow_lid_map` (LID↔טלפון) |
| `whatsapp-bridge/store/<chat>/` | מדיה שהורדה · `send_policy.json` · `api_token` (+`owner_token` ללוח, אותן הרשאות) |
| `agent/agent.db` | `commands`, `claude_runs` (cost, cum, model), `transcripts`, `sent`, `reminders`, `handled`, `kv` (session_id, live_steps, override_until…), טבלאות תוספים |
| `agent/config.json` | תצורה אישית (לא בגיט) — מפתחות ב-`config.example.json` + `deep_model`, `session_max_runs`, `maintainer` |
| `agent/model_override.json` · `agent/models.json` | מודל כפוי (ווצאפ/לוח) · מדריך המודלים (תיאור, עלות, פקודה) |
| `agent/actions.log` · `agent/agent.out` · `whatsapp-bridge/bridge.log` | יומן פעולות של Claude · יומן הסוכן · יומן הגשר |
| `%LOCALAPPDATA%\WhatsAppDash\` | האפליקציה, `settings.json` (נתיב הכלי), webview |
| גיבוי | יומי (`backup_time`) לתיקיית `backup_dir`: db + מדיה, שמירה `backup_keep_days` |

## 5. לוח הבקרה

`server.py`: `GET /data` (כל המצב בבקשה אחת), `POST /action` (`say`, `model`, `draft_decide`, `pause`/`resume`, `restart` bridge|agent|all, `backup`,
`group_info`/`group_rename`/`group_create`, `git_status`/`git_commit`/`release`, `tool_update_*`, `update_*`).
`static/app.js`: כותרת מצב, סרגל חיבור 24ש', שיחה (בועות, בועת מצב עם שלבים, הודעה אופטימית באנטר, תפריט לחצן ימני),
מגירות (טיוטות/נשלחו/התקבלו/תמלולים/עלות), עמודה: קבוצת בקרה · מערכת · מודל · תצוגה (גודל כתב/ממשק, גופן, רקע — localStorage).
"שמור בגיט" = `git add -u` (קבצים חדשים מוצגים עם שאלה מה לעשות). שחרור: קבצי תמונה/אקסל/PDF בגיט → שאלה (`--allow-files` להמשך).
**חשוב:** האפליקציה השולחנית מכילה עותק ארוז של `static/` — שינוי בלוח מגיע אליה רק בשחרור גרסה; בדפדפן (8770) — מיד.

## 6. גרסאות, עדכון ושחרור

- `release.cmd X.Y.Z "הערות"` (או `--notes-file=`): מעדכן גרסה בכל הרכיבים + CHANGELOG, מריץ go vet/test, pytest של הסוכן, ייבוא MCP,
  `claude plugin validate`, **סריקת מידע אישי**, commit + tag + push (ענף `enhanced` → `main`).
- GitHub Actions (`.github/workflows/build.yml`) בונה `whatsapp-bridge.exe`, `WhatsAppDash-X.Y.Z.exe`, `latest.json` ומפרסם Release עם הערות הגרסה בלבד.
- משתמשים: הודעה בבוקר בקבוצה → "בדוק עדכון לכל הכלי" בלוח / `update.cmd` (עוצר, מעדכן, בודק, חוזר אחורה אם נכשל).
- בדיקות: `whatsapp-bridge/*_test.go`, `agent/tests/`, `tools/selftest.py` (חי, 21 שלבים; שלב מקצה-לקצה נכשל אם הסוכן עסוק).

## 7. כללי בטיחות (לא לשבור)

1. אף הודעה לאחרים בלי אישור מפורש של הבעלים — אישור אחד = הודעה אחת לנמען אחד. אין מצב auto, אין אישור לזמן.
2. עברית לכלי הגשר רק מקובץ UTF-8 (`wa_admin.py`), ולאמת בקריאה חוזרת.
3. לא לערוך `run-*.cmd` בזמן שהלולאות רצות. ב-`agent/` תמיד `uv run --no-sync`.
4. כל שינוי תצורה/קוד בסוכן נכנס לתוקף רק אחרי הפעלה מחדש (`restart.request`), ולוודא "agent starting" ביומן.
5. פרסום גרסה (push/release) רק בהוראה מפורשת.

## 8. קבצים אישיים (לא בגיט, קיימים רק בהתקנה)

`agent/config.json`, `agent/home/` (CLAUDE.md אישי, handoff.md, outbox), `TODO.md` (משימות ודרישות הבעלים), `agent/COST-CHANGES.md`,
`whatsapp-bridge/store/`, `agent/agent.db`, `agent/assets/`, `agent/home/SYSTEM-MAP.md` (מפה אישית: מזהי קבוצות, מספרים, נתיבים).

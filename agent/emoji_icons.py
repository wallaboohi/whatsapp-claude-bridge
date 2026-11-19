"""Owner 18.11: 🔥 on one of the owner's own messages adds fitting emoji to it — after the words they describe, his text and order
untouched. Example: "מחר ב-8 פגישה עם הספק על המשאית" → "מחר ב-8 ⏰ פגישה 🤝 עם הספק 🏭 על המשאית 🚚".

Pure module (no I/O): a large Hebrew keyword → emoji table with prefix (ו ה ב ל מ ש כ) and suffix (plural / construct /
possessive) stemming, a density limit (about one emoji per two words), no emoji used twice in one message, and an optional
AI fallback (a callable the agent passes in) for words the table does not know. The text itself is never rewritten: the
code only inserts " <emoji>" after a word, so whatever the table or the AI returns, the owner's words stay exactly as written."""
import json
import os
import re

# ---------------------------------------------------------------------------------------------------------------------------
# The table. Each line: "<words, singular / base form, separated by spaces> : <emoji alternatives, first = preferred>".
# Alternatives keep a message varied: when the first one is already used in the message, the next one is taken.
# Multi-word phrases are written with "_" ("חדר_אוכל") and are matched before single words.
# Category (section name) = adjacent words of the same category get one emoji (the last one): "מחר ב-8" → only ⏰ after ב-8.
# Priority: higher wins when the message has more matches than its emoji budget.
# ---------------------------------------------------------------------------------------------------------------------------
_TABLE = {
    ("time", 3): """
        שעה שעות שעון בשעה : ⏰ 🕗 ⌚
        דקה דקות רגע שניה שנייה : ⏱️ ⏳
        בוקר בבוקר : 🌅 ☀️
        צהריים צהרים בצהריים : 🌞 🕛
        ערב בערב : 🌆 🌇
        לילה בלילה חצות : 🌙 🌃
        היום : 📆 🗓️
        מחר מחרתיים אתמול שלשום : 📅 🗓️
        תאריך יום ימים שבוע חודש שנה רבעון : 📅 🗓️ 📆
        ראשון שני שלישי רביעי חמישי שישי שבת סופש סוף_שבוע : 📅 🗓️
        דדליין מועד יעד לוח_זמנים לו"ז לוז : ⏳ 📆
        עכשיו מיד דחוף בהקדם : ⚡ 🚨
        איחור מאחר מאחרים איחרתי מאוחר : 🐢 ⌛
        מוקדם מוקדמת : 🐓 🌄
        חג חגים פסח סוכות חנוכה פורים ראש_השנה : 🎉 🕎
        יום_הולדת : 🎂 🥳
        חופש חופשה : 🏖️ 🌴
    """,
    ("meeting", 3): """
        פגישה פגישת ישיבה ישיבת מפגש : 🤝 👥
        כנס הרצאה סדנה הדרכה תדריך : 🎤 🧑‍🏫
        ראיון : 🗣️ 🎙️
        הסכם עסקה חוזה : 🤝 ✍️ 📜
        תיאום : 🔄 🗓️
    """,
    ("people", 2): """
        ספק ספקים ספקית : 🏭 📦
        לקוח לקוחה : 🧑‍💼 🤝
        מנהל מנהלת מנכ"ל מנכל בוס אחראי ממונה : 👔 🧑‍💼
        מנהל_עבודה מנהל_אתר מהנדס מהנדסת : 👷 📐
        עובד עובדת עובדים צוות פועל פועלים : 👷 🧑‍🏭 👥
        קבלן קבלנים קבלן_משנה : 👷‍♂️ 🏗️
        מאבטח מאבטחים שומר שומרים קב"ט קבט סייר : 💂 🛡️
        שוטר שוטרים משטרה : 👮 🚓
        חייל חיילים צבא צה"ל צהל מילואים : 🪖 🎖️
        נהג נהגים נהגת : 🧑‍✈️ 🚘
        טכנאי חשמלאי אינסטלטור מכונאי : 🧑‍🔧 🔧
        רופא רופאה חובש פרמדיק אחות : 🧑‍⚕️ 🩺
        עורך_דין עו"ד עוד עורכת_דין שופט שופטת : ⚖️ 🧑‍⚖️
        מורה גננת : 🧑‍🏫 📚
        ילד ילדה ילדים ילדות תינוק תינוקת : 🧒 👶
        בן בת : 👦 👧
        אבא אמא הורים הורה : 👨‍👩‍👧 👪
        אישה אשה נשים : 👩 💁‍♀️
        איש אנשים גבר גברים : 👨 🧑
        חבר חברה חברים : 🫂 👫
        משפחה : 👨‍👩‍👧‍👦 🏡
        סבא סבתא : 👴 👵
        אורח אורחים : 🛎️ 🧳
        שכן שכנים : 🏘️ 👋
        מתנדב מתנדבים : 🙋 💪
        סגן סגנית : 🎖️ 🧑‍💼
    """,
    ("vehicle", 3): """
        משאית טנדר : 🚚 🛻
        רכב אוטו מכונית רכבים : 🚗 🚙
        ג'יפ גיפ : 🚙 🛻
        אוטובוס הסעה הסעות מיניבוס : 🚌 🚐
        מונית : 🚕 🚖
        אופנוע קטנוע : 🏍️ 🛵
        אופניים : 🚲 🚴
        רכבת : 🚆 🚄
        מטוס טיסה טיסות : ✈️ 🛫
        אונייה ספינה סירה : 🚢 ⛵
        מסוק : 🚁 🛩️
        טרקטור מחפר באגר שופל : 🚜 🏗️
        מנוף עגורן : 🏗️ 🪝
        מלגזה : 🏗️ 📦
        אמבולנס : 🚑 🏥
        כבאית : 🚒 🧯
        גרר גרירה : 🚛 🪝
        דלק סולר בנזין תדלוק תחנת_דלק : ⛽ 🛢️
        צמיג צמיגים פנצ'ר פנצר : 🛞 🔧
        חניה חנייה חניון : 🅿️ 🚗
        כביש כבישים דרך מסלול : 🛣️ 🗺️
        פקק פקקים תנועה : 🚦 🚥
        רמזור : 🚦 🚥
        טסט רישוי רישיון רשיון : 🪪 📄
        ק"מ קילומטר קילומטרים קמ : 📏 🛣️
        נסיעה נסיעות לנסוע נוסע נוסעת נוסעים נסעתי נסענו : 🚗 🛣️
        משלוח הובלה הובלות שילוח : 📦 🚚
    """,
    ("place", 2): """
        בית בבית הביתה דירה : 🏠 🏡
        משרד משרדים : 🏢 🗄️
        מפעל מחסן מחסנים : 🏭 📦
        אתר אתרים שטח : 📍 🗺️
        מחנה בסיס מוצב : 🏕️ ⛺
        עיר : 🏙️ 🌆
        כפר מושב קיבוץ יישוב ישוב : 🏘️ 🌾
        בית_חולים מרפאה קופת_חולים : 🏥 🩺
        בית_ספר גן בית_ספר : 🏫 🎒
        אוניברסיטה מכללה : 🎓 🏛️
        בנק : 🏦 💳
        דואר : 🏤 📮
        חנות קניון סופר סופרמרקט שוק : 🛒 🏬
        מסעדה בית_קפה קפיטריה : 🍽️ ☕
        מלון צימר לינה : 🏨 🛏️
        חדר חדרים : 🚪 🛏️
        חדר_אוכל : 🍽️ 🥄
        מקלט ממ"ד ממד : 🛡️ 🚪
        בית_כנסת : 🕍 ✡️
        ים חוף : 🌊 🏖️
        הר הרים גבעה : ⛰️ 🏔️
        מדבר נגב ערבה : 🏜️ 🐪
        גבול גבולות : 🛂 🚧
        ירושלים : 🕌 🏛️
        תל_אביב : 🏙️ 🌃
        אילת : 🐠 🏝️
        חו"ל חול : 🌍 ✈️
        שדה_תעופה נתב"ג נתבג : 🛫 🛄
        מעבר מחסום : 🚧 🛂
        כניסה יציאה שער שערים : 🚪 ↔️
    """,
    ("work", 3): """
        גדר גדרות גידור : 🚧 🧱
        עבודה עבודות פרויקט מקטע משמרת משמרות : 🛠️ 🏗️
        בנייה בניה בניין בנין יסודות : 🏗️ 🧱
        בטון יציקה : 🧱 🪨
        ברזל פלדה מתכת : ⚙️ 🔩
        ריתוך רתך רתכת : 👨‍🏭 🔥
        חפירה חפירות חופר : ⛏️ 🕳️
        עפר חול אבן אבנים סלע : 🪨 ⛰️
        צינור צנרת : 🔩 🚰
        חשמל חשמלי כבל כבלים : ⚡ 🔌
        גנרטור : 🔋 ⚡
        מצלמה מצלמות : 📷 📹
        חיישן חיישנים מכ"ם מכם : 📡 🛰️
        אנטנה תורן : 📡 🗼
        תאורה פנס פנסים : 💡 🔦
        אבטחה ביטחון בטחון מיגון בטיחות : 🛡️ 🦺
        קסדה קסדות : ⛑️ 🪖
        אפוד אפודים וסט ווסט : 🦺 👷
        חליפה חליפות סרבל סרבלים ביגוד : 🥼 👕
        חולצה חולצות מכנס מכנסיים : 👕 👖
        נעל נעליים מגף מגפיים : 🥾 👞
        כפפה כפפות : 🧤 ✋
        משקפי_מגן משקפיים : 🥽 👓
        ציוד : 🧰 📦
        כלי כלים ארגז_כלים : 🧰 🛠️
        פטיש : 🔨 🛠️
        מברג : 🪛 🔧
        מפתח_ברגים מפתח_שוודי : 🔧 🔩
        מסור : 🪚 🪵
        מקדחה : 🔩 🛠️
        סולם : 🪜 🧗
        חבל חבלים שרשרת : 🪢 ⛓️
        מנעול מנעולים : 🔒 🔐
        מפתח מפתחות : 🔑 🗝️
        תיקון תיקונים לתקן תקלה תקלות : 🔧 🛠️
        בדיקה בדיקות לבדוק ביקורת : 🔍 ✅
        תחזוקה : 🧰 🔧
        מלאי : 📦 📊
        הזמנה הזמנות להזמין : 🧾 🛒
        רכש קנייה קניות לקנות : 🛒 🛍️
        אספקה : 📦 🚚
        חבילה חבילות ארגז ארגזים קרטון : 📦 🎁
        משטח משטחים : 🪵 📦
        נוכחות : 🕘 📋
        דוח דו"ח דוחות דו"חות : 📊 📑
        סיכום סיכומים : 📝 📌
        תוכנית תכנית תכנון : 🗺️ 📐
        שרטוט שרטוטים מפה מפות : 🗺️ 📐
        משימה משימות מטלה מטלות : ✅ 📌
        בעיה בעיות : ⚠️ ❗
        פתרון פתרונות : 💡 ✅
        סכנה מסוכן אזהרה : ⚠️ ☢️
        תאונה פציעה פצוע : 🚑 🩹
        שריפה אש : 🔥 🧯
        מטף מטפים : 🧯 🔥
        אזעקה : 🚨 📢
        חירום : 🚨 🆘
        פיגוע : 🚨 💥
        ירי : 💥 🎯
        נשק רובה אקדח : 🔫 🎯
        מכשיר_קשר קשר : 📻 🎙️
        רחפן רחפנים : 🛸 📡
        מחשב לפטופ מחשבים : 💻 🖥️
        מדפסת הדפסה : 🖨️ 📄
        שרת רשת אינטרנט וויפי ויפי : 🌐 📶
        תוכנה אפליקציה אפליקציות קוד : 💻 📲
        בוט אוטומציה : 🤖 ⚙️
        סוללה סוללות מטען : 🔋 🔌
    """,
    ("document", 2): """
        מסמך מסמכים טופס טפסים קובץ קבצים : 📄 📁
        מכתב מכתבים : ✉️ 📨
        מייל מיילים דוא"ל אימייל : 📧 📨
        חשבונית חשבוניות קבלה קבלות : 🧾 💳
        הצעת_מחיר : 🧾 💲
        אישור אישורים : ✅ 📃
        חתימה חתימות לחתום : ✍️ 🖊️
        רשימה רשימות טבלה טבלאות : 📋 🗒️
        אקסל גיליון גליון : 📊 📈
        תמונה תמונות צילום צילומים : 📸 🖼️
        סרטון סרטונים וידאו : 🎬 📹
        ספר ספרים : 📚 📖
        מחברת פתק פתקים : 📒 🗒️
        תעודה תעודות : 🎓 📜
        תעודת_זהות ת"ז תז : 🪪 🆔
        דרכון : 🛂 📘
        עט עיפרון : 🖊️ ✏️
        מספר מספרים : 🔢 #️⃣
        סיסמה סיסמא : 🔐 🔑
        חוק חוקים נוהל נהלים כלל כללים : 📜 ⚖️
        תיק : 💼 📁
    """,
    ("money", 2): """
        כסף מזומן : 💰 💵
        שקל שקלים ש"ח שח : 💰 🪙
        דולר דולרים : 💵 💲
        יורו : 💶 💰
        משכורת שכר תשלום תשלומים : 💸 💰
        מחיר מחירים עלות עלויות : 🏷️ 💲
        הנחה מבצע : 🏷️ 🎯
        תקציב : 📊 💰
        חוב חובות : 💳 📉
        אשראי כרטיס_אשראי : 💳 🏧
        העברה_בנקאית : 🏦 💸
        קנס קנסות : 🧾 😬
        רווח הכנסה הכנסות : 📈 💹
        הפסד הוצאה הוצאות : 📉 💸
        מתנה מתנות : 🎁 🎀
    """,
    ("food", 2): """
        אוכל ארוחה ארוחות מנה מנות : 🍽️ 🥘
        ארוחת_בוקר : 🍳 🥐
        ארוחת_צהריים : 🍲 🥗
        ארוחת_ערב : 🍝 🌙
        קפה אספרסו : ☕ 🫘
        תה : 🍵 🫖
        מים בקבוק בקבוקים : 💧 🚰
        שתייה משקה משקאות : 🥤 🧃
        בירה : 🍺 🍻
        יין : 🍷 🍇
        לחם פיתה פיתות לחמניה לחמנייה : 🍞 🥖
        פיצה : 🍕 🧀
        המבורגר : 🍔 🍟
        שווארמה שוארמה פלאפל : 🌯 🥙
        סלט ירקות ירק : 🥗 🥬
        פרי פירות : 🍎 🍉
        תפוח : 🍎 🍏
        בננה : 🍌 🍎
        עוף שניצל : 🍗 🐔
        בשר מנגל על_האש סטייק : 🥩 🍖
        דג דגים : 🐟 🍣
        ביצה ביצים : 🥚 🍳
        גבינה : 🧀 🥛
        חלב : 🥛 🍼
        אורז : 🍚 🍙
        פסטה : 🍝 🍜
        מרק : 🍲 🥣
        עוגה עוגות : 🍰 🎂
        עוגיה עוגיות : 🍪 🥠
        שוקולד : 🍫 🍬
        גלידה : 🍦 🍨
        ממתק ממתקים : 🍬 🍭
        קייטרינג קיטרינג מגש מגשים : 🍱 🍽️
        מטבח לבשל בישול : 🍳 👩‍🍳
        קרח : 🧊 ❄️
        רעב רעבים : 😋 🍴
    """,
    ("weather", 2): """
        שמש שמשי : ☀️ 🌞
        גשם גשמים : 🌧️ ☔
        סערה סופה ברק ברקים : ⛈️ 🌩️
        שלג : ❄️ ☃️
        רוח רוחות : 💨 🌬️
        חום שרב חמסין : 🥵 🌡️
        קור : 🥶 ❄️
        ענן עננים ערפל : ☁️ 🌫️
        מזג_אוויר מזג_האוויר : 🌤️ 🌡️
        שקיעה זריחה : 🌅 🌄
        ירח : 🌙 🌕
        כוכב כוכבים : ⭐ ✨
        אבק : 🌪️ 😷
        בוץ : 🟫 🥾
        שיטפון הצפה : 🌊 🚱
    """,
    ("nature", 1): """
        עץ עצים : 🌳 🌲
        פרח פרחים : 🌸 💐
        צמח צמחים עשב דשא : 🌱 🌿
        כלב כלבים : 🐕 🐶
        חתול חתולים : 🐈 🐱
        נחש נחשים : 🐍 ⚠️
        ציפור ציפורים : 🐦 🕊️
        סוס סוסים : 🐎 🐴
        פרה פרות : 🐄 🐮
        כבש כבשים : 🐑 🐏
        חרק חרקים יתוש יתושים : 🦟 🐜
        עקרב : 🦂 ⚠️
        זבוב זבובים : 🪰 🦟
    """,
    ("comm", 2): """
        טלפון פלאפון נייד סלולרי : 📱 ☎️
        שיחה שיחות להתקשר התקשר התקשרתי מתקשר : 📞 ☎️
        הודעה הודעות : 💬 📩
        ווצאפ וואטסאפ וואצאפ : 💬 📲
        קבוצה קבוצות : 👥 💬
        הקלטה הקלטות : 🎙️ 🎧
        שאלה שאלות : ❓ 🤔
        תשובה תשובות : 💬 ✅
        עדכון עדכונים לעדכן : 🔔 📢
        הודעה_לציבור הכרזה פרסום מודעה : 📢 📣
        תזכורת תזכורות : 🔔 ⏰
        סקר סקרים : 📊 🗳️
        הזמנה_לאירוע אירוע אירועים : 🎟️ 🎉
        מסיבה חגיגה : 🥳 🎊
        חתונה : 💍 👰
        לוויה הלוויה ניחום שבעה : 🕯️ 🖤
        ברית : 👶 ✡️
        בר_מצווה בת_מצווה : 🎉 ✡️
    """,
    ("health", 2): """
        חולה מחלה חום_גוף : 🤒 🤧
        תרופה תרופות כדור כדורים : 💊 🩹
        זריקה חיסון : 💉 🩺
        בדיקת_דם : 🩸 🧪
        שיניים רופא_שיניים : 🦷 🪥
        עייף עייפה עייפים : 😴 🥱
        שינה לישון ישן : 😴 🛌
        ספורט אימון כושר : 🏋️ 💪
        ריצה לרוץ : 🏃 👟
        הליכה ללכת : 🚶 👣
        כדורגל : ⚽ 🥅
        כדורסל : 🏀 ⛹️
        שחייה בריכה : 🏊 🩱
        מוזיקה שיר שירים : 🎵 🎶
        סרט סרטים : 🎬 🍿
        משחק משחקים : 🎮 🎲
        טיול טיולים : 🥾 🗺️
        תפילה להתפלל : 🙏 🕍
        שבת_שלום : 🕯️ 🍷
    """,
    ("feeling", 2): """
        תודה תודות מודה : 🙏 💐
        בהצלחה : 🍀 💪
        מזל_טוב : 🎉 🥂
        כל_הכבוד ישר_כוח יפה_מאוד : 👏 🏆
        מצוין מעולה מושלם סבבה אחלה : 👌 💯
        שמח שמחה שמחים : 😊 😄
        עצוב עצובה עצב : 😢 😔
        כועס כועסת כעס עצבני : 😠 😤
        מפחד פחד חושש : 😨 😰
        לחץ לחוץ לחוצה מתח : 😬 😵‍💫
        אהבה אוהב אוהבת : ❤️ 🥰
        הפתעה מופתע : 😲 🎁
        צחוק מצחיק : 😂 🤣
        מתנצל סליחה מצטער : 🙇 😔
        בעייתי : 😕 ⚠️
        שלום היי ביי להתראות : 👋 🙂
        בוקר_טוב : 🌞 ☕
        לילה_טוב : 🌙 😴
        ערב_טוב : 🌆 ✨
        הצלחה ניצחון : 🏆 🎯
        כישלון כשלון : 💔 📉
        רעיון רעיונות : 💡 🧠
        חשוב חשובה : ❗ 📌
        סוד סודי : 🤫 🔒
        מוכן מוכנה מוכנים : ✅ 👍
        בטוח : 👍 💯
        אולי : 🤷 🤔
        כן : 👍 ✔️
        לא_בטוח : 🤔 🤷
        נגמר סיימתי הסתיים בוצע : ✅ 🏁
        התחלה להתחיל מתחילים : 🚀 🏁
        חדש חדשה חדשים : 🆕 ✨
        ישן ישנה : 🗃️ 🕰️
        מהר מהיר : 💨 ⚡
        לאט איטי : 🐢 🐌
        גדול גדולה ענק : 🐘 📏
        קטן קטנה : 🐜 🤏
        חם חמה : 🥵 🌡️
        קר : 🥶 🧊
        יקר יקרה : 💸 💎
        זול זולה : 🏷️ 👛
        נקי ניקיון לנקות : 🧹 ✨
        מלוכלך לכלוך זבל פסולת : 🗑️ 🧹
        שבור שבורה נשבר : 💔 🔨
        אבוד אבד נעלם : 🔎 ❓
        מצאתי נמצא : 🔎 🎯
        סגור סגורה נסגר : 🔒 🚫
        פתוח פתוחה נפתח : 🔓 🟢
        ביטול בוטל לבטל : ❌ 🚫
        אסור : 🚫 ⛔
        מותר : ✅ 🟢
    """,
    ("chat", 2): """
        ברור נכון בטח בדיוק כמובן : ✅ 💯
        הרבה המון מלא : 💯 📈
        אח אחי אחותי אחיי אחים אחשלי : 🤜 🫂
        יפה יופי מהמם מהממת : 😍 ✨
        צוחק צוחקת צוחקים : 😂 🤣
        וואו וואי מרתק מדהים מדהימה : 🤩 😮
        מטורף מטורפת שיגעון משוגע : 🤯 🌀
        כיף בכיף כיפי : 😎 🎈
        חזק חזקה חזקים : 💪 🦾
        מגניב מגניבה : 😎 🆒
        למעלה מעל : ⬆️ 🔝
        למטה מתחת : ⬇️ 🔻
        ריסוס לרסס מרסס מרססים ריסס : 💦 🌫️
        גומרים גמור גמורים הורגים : 😵 🥴
        מה_קרה איך_קרה : 😮 ❓
        שאלה שאלות : ❓ 🤔
        תשובה תשובות : 💬 ✅
        נשיקה נשיקות : 😘 💋
        חיבוק חיבוקים : 🤗 🫂
        טוב טובה מעולה מצוין מצויין סבבה : 👍 👌
        בסדר אוקיי אוקי : 👌 🆗
    """,
    ("action", 1): """
        עזרה לעזור עוזר : 🆘 🤝
        לשלוח שלחתי נשלח שליחה : 📤 📨
        לקבל קיבלתי התקבל : 📥 📬
        להביא הבאתי : 🫴 📦
        לחכות מחכה מחכים המתנה : ⏳ 🪑
        לחשוב חושב : 🤔 💭
        לכתוב כותב כתבתי : ✍️ 📝
        לקרוא קורא קראתי : 📖 👀
        לראות רואה ראיתי : 👀 🔭
        לשמוע שומע שמעתי : 👂 🎧
        לאכול אוכלים : 😋 🍴
        לשתות שותים : 🥤 💧
        לישון : 😴 🛌
        לעבוד עובדים_קשה : 💪 🛠️
        לנוח מנוחה הפסקה : 🧘 ☕
        לחזור חוזר חזרתי : 🔙 🔁
        להגיע מגיע מגיעה מגיעים הגעתי הגיע הגיעו : 📍 🏁
        לצאת יוצא יצאתי : 🚶 🚪
        לבנות בונה בונים : 🏗️ 🧱
        למכור מכירה : 🏷️ 💲
        להעביר העברה העברתי : 🔀 ➡️
        לשמור שמירה : 💾 🛡️
        למחוק מחיקה : 🗑️ ❌
        לנצח ניצחנו : 🏆 🥇
    """,
    ("misc", 1): """
        ראשון_במקום מקום_ראשון : 🥇 🏆
        מקום : 📍 🧭
        מטרה מטרות : 🎯 🏹
        תוצאה תוצאות : 📊 🏁
        גרף גרפים נתונים סטטיסטיקה : 📈 📊
        אחוז אחוזים : 💯 📊
        כמות מספר_עובדים : 🔢 📊
        שאלון : 📝 ❓
        ארון ארונות : 🗄️ 🚪
        שולחן שולחנות : 🪑 🍽️
        כיסא כיסאות כסא : 🪑 💺
        מיטה מיטות : 🛏️ 😴
        מזגן מזגנים : ❄️ 🌬️
        מקרר מקררים : 🧊 🥶
        מים_חמים דוד_שמש : 🚿 ♨️
        מקלחת מקלחות : 🚿 🧼
        שירותים : 🚻 🧻
        כביסה מכונת_כביסה : 🧺 👕
        חלון חלונות : 🪟 🌤️
        דלת דלתות : 🚪 🔑
        קיר קירות חומה : 🧱 🏰
        גג גגות : 🏠 ☂️
        מדרגות : 🪜 ⬆️
        מעלית : 🛗 ⬆️
        שקית שקיות : 🛍️ 🗑️
        שעון_נוכחות : 🕘 💳
        כוכב_צפון : ⭐ 🧭
        דגל דגלים : 🚩 🇮🇱
        ישראל : 🇮🇱 ✡️
        עולם : 🌍 🌐
    """,
}

_FINAL = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}
_UNFINAL = {v: k for k, v in _FINAL.items()}
PREFIXES = "והבלמשכ"


def _final(s):
    return s[:-1] + _FINAL.get(s[-1], s[-1]) if s else s


def _build():
    words, phrases = {}, {}
    for (cat, prio), block in _TABLE.items():
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            left, right = line.rsplit(" : ", 1) if " : " in line else line.rsplit(":", 1)
            emojis = right.split()
            for w in left.split():
                entry = (tuple(emojis), cat, prio)
                if "_" in w:
                    phrases.setdefault(tuple(w.split("_")), entry)
                else:
                    words.setdefault(w, entry)
    return words, phrases


WORDS, PHRASES = _build()


def _load_cldr():
    """Owner 19.11 11:23: a context table for ALL the icons, not only the ~500 of the hand table. emoji_he.json (built by
    tools/build_emoji_he.py from the Unicode CLDR Hebrew keywords) reaches ~1,950 emoji; it is the second layer: it answers
    only words / phrases the hand table does not know, at the lowest priority."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_he.json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}, {}
    words = {w: (tuple(e), "cldr", 1) for w, e in (d.get("words") or {}).items() if e}
    phrases = {tuple(p.split()): (tuple(e), "cldr", 1) for p, e in (d.get("phrases") or {}).items() if e and '"' not in p}
    return words, phrases


CLDR_WORDS, _CLDR_PHRASES = _load_cldr()
# CLDR keywords that are mostly another word in a message (בקר = "visited / control" far more than "cattle")
CLDR_SKIP = set("בקר מקום רגיל סוף שם ראש גוף צד".split())
for _k, _v in _CLDR_PHRASES.items():
    PHRASES.setdefault(_k, _v)
MAX_PHRASE = max((len(p) for p in PHRASES), default=1)

_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿←-⇿™ℹ〰〽"
                       "㊗㊙©®‼⁉Ⓜ▪-◾⤴⤵️‍⃣#*0-9"
                       "\U0001F1E6-\U0001F1FF\U0001F3FB-\U0001F3FF]")
_HAS_PICTO = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿←-⇿‼⁉™ℹ"
                        "▪-◾⤴⤵〰〽㊗㊙©®Ⓜ⃣]")
_LETTERS = re.compile(r"[A-Za-zא-ת]")
_CORE = re.compile(r"^([\"'(\[*_~„“«]*)(.*?)([\"')\].,!?:;*_~…״”»]*)$", re.S)
_TIME = re.compile(r"^(?:[בל]-?|ב?שעה-?)?\d{1,2}(?::\d{2})?$")
_CLOCK = re.compile(r"^(?:[בל]-?)?\d{1,2}:\d{2}$")
_DATE = re.compile(r"^(?:[בלמ]-?)?\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?$")
_MONEY = re.compile(r"^\d[\d,.]*₪$|^₪\d|^\$\d|^\d[\d,.]*\$$")
_PERCENT = re.compile(r"^\d[\d.]*%$")
STOP = set("""את של על עם אל אם כי גם או אבל רק עוד כבר לא כן זה זו זאת הוא היא הם הן אני אתה את אנחנו אתם אתן מה מי איך למה מתי איפה
              יש אין כל כמה הרבה מאוד קצת אז פה שם כאן לי לך לו לה לנו לכם להם שלי שלך שלו שלה שלנו שלהם אותו אותה אותם אצל בין
              לפני אחרי תוך בלי עד מול ליד כמו אחד אחת שתי שני שלוש ארבע חמש
              קרה קרו קורה קורים קרתה""".split())  # owner 19.11: "קרה" = happened, not cold (it got a 🧊)


def is_emoji(s):
    """True for a short string made only of emoji code points (with ZWJ / variation selectors / skin tones / keycaps)."""
    s = (s or "").strip()
    return 0 < len(s) <= 12 and bool(_HAS_PICTO.search(s)) and all(_EMOJI_RE.match(c) for c in s)


def candidates(word):
    """Base forms to look up for one Hebrew word, most literal first: the word itself, then without prefix letters
    (ו ה ב ל מ ש כ, up to three: "ובמשאית", "שבבית"), each with suffix variants (plural ים / ות, construct ת / י,
    possessive ו / נו / ך)."""
    w = word.replace("״", '"').replace("׳", "'")
    out = [w]
    stems = [(w, 0)]
    for i in range(1, 4):
        if len(w) - i < 2 or w[i - 1] not in PREFIXES:
            break
        stems.append((w[i:], i))
    for s, np in stems:
        forms = [s]
        if len(s) >= 4 and s.endswith("ים"):
            forms += [_final(s[:-2]), s[:-2] + "ה", s[:-2]]
        if len(s) >= 4 and s.endswith("ות"):
            forms += [s[:-2] + "ת", s[:-2] + "ה", _final(s[:-2]), s[:-1]]
        if len(s) >= 4 and s.endswith("ת"):
            forms += [s[:-1] + "ה"]
        if len(s) >= 4 and s.endswith("י"):
            forms += [_final(s[:-1]), s[:-1] + "ה"]
        if len(s) >= 4 and s.endswith("נו"):
            forms += [_final(s[:-2])]
        if len(s) >= 4 and s[-1] in "וך":
            forms += [_final(s[:-1])]
        for f in forms:
            # a two-letter base form only from the word itself or with a single ה / ב / ל prefix ("הים", not "שקר" → "קר")
            if len(f) < 2 or (len(f) == 2 and np > 1) or (len(f) == 2 and np == 1 and w[0] != "ה") \
                    or (len(f) == 2 and f != s):
                continue
            if f not in out:
                out.append(f)
    return out


def lookup(word):
    """(emojis, category, priority) for a word, or None."""
    cands = candidates(word)
    for c in cands:
        if c in STOP and c == word:
            return None
        if c in WORDS:
            return WORDS[c]
    for c in cands:  # second layer: the CLDR keywords of every emoji (only what the hand table does not know)
        if c in CLDR_WORDS and c not in STOP and c not in CLDR_SKIP:
            return CLDR_WORDS[c]
    return None


def _special(core):
    if _CLOCK.match(core) or (_TIME.match(core) and not core[:1].isdigit()):
        return (("⏰", "🕗", "⌚"), "time", 3)
    if _DATE.match(core):
        return (("📅", "🗓️"), "time", 3)
    if _MONEY.match(core):
        return (("💰", "💵"), "money", 2)
    if _PERCENT.match(core):
        return (("📊", "💯"), "misc", 1)
    return None


def _tokens(text):
    """[(start, end, core, core_end)] for each whitespace-separated token: core = the word without surrounding quotes /
    formatting marks / punctuation; core_end = where the emoji goes (after closing * _ ~ marks, before . , ! ? :)."""
    out = []
    for m in re.finditer(r"\S+", text):
        tok = m.group(0)
        c = _CORE.match(tok)
        lead, core, trail = c.group(1), c.group(2), c.group(3)
        marks = re.match(r"^[*_~]*", trail).group(0)
        out.append((m.start(), m.end(), core, m.start() + len(lead) + len(core) + len(marks)))
    return out


BOLD_CATS = {"time", "money", "health"}  # besides any priority-3 word (time, meeting, vehicle, work)


def _bold_pick(text, toks, hits, nwords):
    """Owner 19.11 11:23: "the sign should also shape the font, bold the important words". Important = when, how much and
    the main subject: times / dates / money / percent and the priority-3 words (meeting, vehicle, work), at most one word in
    five (1-4 per message). Never inside a quote / code block, never a word that already carries * _ ~ formatting."""
    if "```" in text or nwords < 3:
        return set()
    out = []
    for k, (emojis, cat, prio) in hits.items():
        start, end, core, _ = toks[k]
        tok = text[start:end]
        if any(m in tok for m in "*_~`") or not core or is_emoji(core):
            continue
        line_start = text.rfind("\n", 0, start) + 1
        if text[line_start:start].lstrip().startswith(">"):
            continue
        if prio >= 3 or cat in BOLD_CATS:
            out.append((-prio, k))
    cap = max(1, min(4, nwords // 5))
    picked = set()
    for _, k in sorted(out):
        if len(picked) < cap and k - 1 not in picked and k + 1 not in picked:  # "*שלושה* *ימים*" reads as noise
            picked.add(k)
    return picked


def plan(text):
    """The table pass. Returns (tokens, picks, used, budget, bold): bold = token indexes to wrap in *...*; picks = {token index: emoji}; used = emoji already in the text or picked."""
    toks = _tokens(text)
    used = {t[2] for t in toks if is_emoji(t[2])}
    words = [i for i, t in enumerate(toks) if _LETTERS.search(t[2]) or re.search(r"\d", t[2])]
    budget = max(1, (len(words) + 1) // 2)
    hits = {}  # token index -> (emojis, cat, prio)
    i = 0
    while i < len(toks):
        core = toks[i][2]
        if not core or is_emoji(core) or core.startswith(("http", "www.", "@")) or "/" in core:
            i += 1
            continue
        done = False
        for n in range(min(MAX_PHRASE, len(toks) - i), 1, -1):  # phrases first: "ארוחת בוקר", "בית ספר"
            seq = [toks[i + k][2] for k in range(n)]
            for first in candidates(seq[0])[:4]:
                key = tuple([first] + seq[1:])
                if key in PHRASES:
                    hits[i + n - 1] = PHRASES[key]
                    i, done = i + n, True
                    break
            if done:
                break
        if done:
            continue
        hit = _special(core) or (lookup(core) if _LETTERS.search(core) else None)
        if hit:
            hits[i] = hit
        i += 1
    # adjacent time words: one emoji, after the last of them ("מחר ב-8" → only after ב-8)
    for k in sorted(hits):
        if k + 1 in hits and hits[k][1] == hits[k + 1][1] == "time":
            del hits[k]
    # an emoji already standing right after a word: leave that word alone (🔥 twice adds nothing twice)
    for k in list(hits):
        if k + 1 < len(toks) and is_emoji(toks[k + 1][2]):
            del hits[k]
    bold = _bold_pick(text, toks, hits, len(words))
    if len(hits) > budget:
        keep = sorted(hits, key=lambda k: (-hits[k][2], k))[:budget]
        hits = {k: hits[k] for k in keep}
    picks = {}
    for k in sorted(hits):
        e = next((x for x in hits[k][0] if x not in used), None)
        if e:
            picks[k] = e
            used.add(e)
    return toks, picks, used, budget, bold


def ai_prompt(text, toks, idxs, used):
    words = "\n".join(f"{k}: {toks[k][2]}" for k in idxs)
    return ("You add emoji icons to a Hebrew WhatsApp message. Message:\n" + text + "\n\nWords (index: word):\n" + words +
            "\n\nFor the words where a clear, fitting emoji exists, choose one — varied and specific (use the whole emoji range, "
            "not generic ones), never one of these already used: " + (" ".join(sorted(used)) or "-") +
            ". Skip words that have no fitting icon (function words, names you do not know). Answer ONLY a JSON object "
            '{"<index>": "<emoji>"} with at most ' + str(max(1, len(idxs) // 2)) + " entries, nothing else.")


def parse_ai(out, allowed, used):
    """{index: emoji} from the model's answer — only indexes we asked about, only real emoji, none repeated."""
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    res, seen = {}, set(used)
    for k, v in (data.items() if isinstance(data, dict) else []):
        try:
            k = int(k)
        except (TypeError, ValueError):
            continue
        v = str(v).strip()
        if k in allowed and is_emoji(v) and v not in seen:
            res[k] = v
            seen.add(v)
    return res


def render(text, toks, picks, bold=()):
    """Inserts " <emoji>" after each picked word and wraps the bold words' core in *...* (owner 19.11). Nothing else of the
    text changes: same words, same order, same punctuation."""
    ins = []  # (position, order at that position, string)
    for k in picks:
        ins.append((toks[k][3], 1, " " + picks[k]))
    for k in bold:
        start, end, core, _ = toks[k]
        at = start + text[start:end].find(core)
        ins.append((at, 2, "*"))
        ins.append((at + len(core), 0, "*"))  # closes before the emoji that follows the same word
    out, last = [], 0
    for pos, _, s in sorted(ins):
        out.append(text[last:pos] + s)
        last = pos
    out.append(text[last:])
    return "".join(out)


def iconize(text, ai=None, bold=True):
    """The owner's text with emoji inserted after the words they fit. ai(prompt) -> str (optional): a small model for the
    words the table does not know; its answer can only add emoji, never change a word. Returns the text unchanged when
    nothing fits. bold: also wrap the important words in *...* (owner 19.11 11:23)."""
    if not (text or "").strip():
        return text
    toks, picks, used, budget, strong = plan(text)
    if ai and len(picks) < budget:
        near = set(picks) | {k + 1 for k in picks} | {k - 1 for k in picks}
        idxs = [k for k, t in enumerate(toks) if k not in near and len(t[2]) >= 3 and _LETTERS.search(t[2])
                and t[2] not in STOP and not is_emoji(t[2]) and not t[2].startswith(("http", "www.", "@"))
                and not (k + 1 < len(toks) and is_emoji(toks[k + 1][2]))]
        if idxs:
            try:
                extra = parse_ai(ai(ai_prompt(text, toks, idxs, used)), set(idxs), used)
            except Exception:  # noqa: BLE001 — the AI is optional: the table result stands
                extra = {}
            for k in sorted(extra):
                if len(picks) >= budget:
                    break
                picks[k] = extra[k]
    return render(text, toks, picks, strong if bold else ())


def _load_legend():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_legend.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


LEGEND = _load_legend()  # owner 19.11: icon -> Hebrew words that describe it (tools/build_emoji_legend.py)


def by_description(desc, used=(), n=1):
    """Owner 19.11: pick icon(s) BY DESCRIPTION from the legend: the icon whose description words match most of the words of
    `desc` (a word = exact or a prefix-stripped form), earlier words in the icon's list weigh more. Icons in `used` are skipped."""
    words = {c for w in re.split(r"[\s,/·]+", desc or "") if w for c in candidates(w)}
    scored = []
    for emo, ws in LEGEND.items():
        if emo in used:
            continue
        s = sum(1.0 / (1 + i * 0.05) for i, w in enumerate(ws) if w in words or any(p in words for p in w.split()))
        if s:
            scored.append((s, emo))
    scored.sort(key=lambda t: -t[0])
    return [e for _, e in scored[:n]]

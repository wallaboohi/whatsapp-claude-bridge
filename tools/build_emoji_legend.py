"""Owner 19.11.2026 (the icon legend): builds agent/emoji_legend.json + agent/emoji_legend.md from the 🔥 tables: for every
icon the Hebrew words that describe it (hand table first, then the Unicode CLDR keywords). The bridge picks an icon BY DESCRIPTION
(emoji_icons.by_description) instead of guessing, so status messages and 🔥 use the whole set.
    uv run --no-project python -X utf8 tools/build_emoji_legend.py"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
import emoji_icons as ei  # noqa: E402
from build_emoji_he import load as load_cldr, NOISE  # noqa: E402

leg = {}
def add(emo, word, src):
    d = leg.setdefault(emo, {"hand": [], "cldr": []})
    if word not in d[src]:
        d[src].append(word)
for w, (emos, cat, pr) in ei.WORDS.items():
    for e in emos:
        add(e, w.replace("_", " "), "hand")
for w, (emos, cat, pr) in ei.CLDR_WORDS.items():
    for e in emos:
        add(e, w, "cldr")
for ph, (emos, cat, pr) in ei.PHRASES.items():
    for e in emos:
        add(e, " ".join(ph), "hand" if cat != "cldr" else "cldr")
# Keep the source annotations for every WhatsApp/Unicode emoji too. The reverse
# lookup deliberately filters generic words, so 121 otherwise valid emoji had
# no entry in the old legend. Direct descriptions make those emoji discoverable
# without weakening the filters used by 🔥 matching.
for source in (os.path.join(os.environ.get("TEMP", "."), "cldr", "he.json"),
               os.path.join(os.environ.get("TEMP", "."), "cldr", "he_der.json")):
    if not os.path.exists(source):
        continue
    for emo, ann in load_cldr(source).items():
        if not ei.is_emoji(emo):
            continue
        names = [n.strip() for n in ann.get("tts", []) if n.strip()]
        names += [n.split(":", 1)[1].strip() for n in names if n.startswith("דגל:")]
        words = names + [w.strip() for w in ann.get("default", [])
                         if w.strip() and w.strip() not in NOISE]
        for word in words:
            add(emo, word, "cldr")
out = {e: (d["hand"] + [x for x in d["cldr"] if x not in d["hand"]]) for e, d in leg.items()}
with open(os.path.join(ROOT, "agent", "emoji_legend.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=0)
with open(os.path.join(ROOT, "agent", "emoji_legend.md"), "w", encoding="utf-8") as f:
    f.write(f"# מקרא אייקונים ({len(out)}): אייקון ← מילים שמתארות אותו\n\n")
    for e, ws in out.items():
        f.write(f"{e} {', '.join(ws)}\n")
print(len(out), "icons")

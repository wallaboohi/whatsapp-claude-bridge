"""Owner 19.11.2026 11:23 ("🔥: a table of possible contexts for ALL the icons, also those not used yet, almost guaranteed use"):
builds agent/emoji_he.json, the second layer of the 🔥 table, from the Unicode CLDR Hebrew emoji keywords (every emoji
WhatsApp shows, with its Hebrew name and keywords). The hand table in emoji_icons.py stays first; this file only answers
words the hand table does not know.

    uv run --no-project python -X utf8 tools/build_emoji_he.py <he.json> [<he_derived.json>]

Sources (public, Unicode license): github.com/unicode-org/cldr-json
  cldr-annotations-full/annotations/he/annotations.json
  cldr-annotations-derived-full/annotationsDerived/he/annotations.json   (flags and other sequences; skin tones skipped)

Output: {"words": {"<word>": ["<emoji>", ...]}, "phrases": {"<w1> <w2>": [...]}, "emoji_count": n}
Order inside a list: the emoji whose own name is that word first, then the most specific (fewest keywords) ones.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
import emoji_icons as ei  # noqa: E402

SKIN = {chr(c) for c in range(0x1F3FB, 0x1F400)}
MAX_PER_WORD = 6
TOO_GENERIC = 25  # a keyword shared by more emoji than this ("פנים", "סמל", "יד") says nothing about any of them
NOISE = set("""סמל סימן צורה פרצוף פנים אדם איש אישה גבר יד אצבע לב עיגול ריבוע משולש מלא חלול קטן גדול בינוני כפתור
               שחור לבן אדום כחול ירוק צהוב כתום סגול חום אפור דגל אות מספר חץ קו כוכב חיוך אמוג׳י אימוג׳י""".split())


def load(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("annotations", d.get("annotationsDerived", {})).get("annotations", {})


def main(paths):
    entries = {}
    for p in paths:
        for emo, a in load(p).items():
            if any(c in SKIN for c in emo) or not ei.is_emoji(emo):
                continue
            names = [n.strip() for n in a.get("tts", []) if n.strip()]
            # flags: "דגל: ישראל" -> the country name alone is the keyword ("ישראל" -> 🇮🇱)
            names += [n.split(":", 1)[1].strip() for n in names if n.startswith("דגל:")]
            names = [n for n in names if ":" not in n]
            kws = [k.strip() for k in a.get("default", []) if k.strip()]
            entries.setdefault(emo, (names, kws))
    words, phrases = {}, {}
    for emo, (names, kws) in entries.items():
        spec = len(kws)
        for rank, kw in [(0, n) for n in names] + [(1, k) for k in kws]:
            kw = kw.replace("״", '"').replace("׳", "'")
            parts = [w for w in kw.split() if w]
            if not parts:
                continue
            if len(parts) == 1:
                w = parts[0]
                if len(w) < 2 or w in ei.STOP or w in NOISE or not ei._LETTERS.search(w):
                    continue
                words.setdefault(w, {}).setdefault(emo, (rank, spec))
            elif 2 <= len(parts) <= 3 and not all(x in ei.STOP or x in NOISE for x in parts):
                phrases.setdefault(" ".join(parts), {}).setdefault(emo, (rank, spec))
    def order(d):
        return [e for e, _ in sorted(d.items(), key=lambda kv: kv[1])][:MAX_PER_WORD]
    out_w = {w: order(d) for w, d in words.items() if len(d) <= TOO_GENERIC}
    out_p = {p: order(d) for p, d in phrases.items() if len(d) <= TOO_GENERIC}
    covered = {e for v in list(out_w.values()) + list(out_p.values()) for e in v}
    out = {"source": "Unicode CLDR he annotations (cldr-json)", "emoji_count": len(entries), "covered": len(covered),
           "words": dict(sorted(out_w.items())), "phrases": dict(sorted(out_p.items()))}
    dst = os.path.join(ROOT, "agent", "emoji_he.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print(f"{dst}: {len(entries)} emoji, {len(covered)} reachable by a word, {len(out_w)} words, {len(out_p)} phrases")


if __name__ == "__main__":
    main(sys.argv[1:] or [os.path.join(os.environ.get("TEMP", "."), "cldr", "he.json")])

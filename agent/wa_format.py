"""WhatsApp text formatting as bridge tools (owner Yakir, 17.11.2026).

WhatsApp renders these (phone, desktop and web), all typed as plain characters:
    *bold*      _italic_      ~strikethrough~      `inline code`      ```code block```
    "- item" or "* item"  = bullet list      "1. item" = numbered list      "> text" = quote
Not supported: underline, colours, font sizes, headings, tables, horizontal rules (they show as raw characters).
The long-press / selection menu of the app inserts exactly these characters, so text written this way looks the same.
Hebrew (right-to-left): a line that starts with a Latin letter or a digit is laid out left-to-right; the right-to-left mark (RLM)
at its start keeps the whole line aligned to the right.

Helpers return strings; md_to_wa(text) converts what an AI usually writes (Markdown) to what WhatsApp understands.
"""
import re

RLM = "‏"
FORMAT_GUIDE = """עיצוב בווצאפ (הכלים: agent/wa_format.py):
*הדגשה* · _נטוי_ · ~קו חוצה~ · `קוד בשורה` · ```בלוק קוד``` · "- פריט" רשימה · "1. פריט" רשימה ממוספרת · "> ציטוט".
אין קו תחתון, צבעים, כותרות, טבלאות וקווים מפרידים. שורה שנפתחת באנגלית או במספר מיושרת שמאלה: מקדימים לה את סימן הימין-לשמאל.
כותרת = שורה בהדגשה. הפרדה בין חלקים = שורה ריקה, לא קו."""


def bold(t):
    return f"*{t}*"


def italic(t):
    return f"_{t}_"


def strike(t):
    return f"~{t}~"


def mono(t):
    return f"`{t}`"


def code_block(t):
    return f"```{t}```"


def quote(t):
    return "\n".join(f"> {l}" for l in str(t).split("\n"))


def bullets(items):
    return "\n".join(f"- {i}" for i in items)


def numbered(items):
    return "\n".join(f"{n}. {i}" for n, i in enumerate(items, 1))


def rtl(t):
    """Every line of t starts on the right (RLM), also when it begins with Latin letters or a digit."""
    return "\n".join((RLM + l) if l.strip() and not l.startswith(RLM) else l for l in str(t).split("\n"))


def heading(t):
    return f"*{t}*"


def md_to_wa(text):
    """Markdown -> WhatsApp: **b** -> *b*, ~~s~~ -> ~s~, '# H' -> *H*, [t](u) -> 't: u', tables -> lines, rules removed.
    Code blocks (```) are left exactly as written."""
    out, fenced = [], False
    for line in str(text or "").split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        s = line
        if re.fullmatch(r"\s*([-*_=])\1{2,}\s*", s):  # a horizontal rule: owner 17.11 — lines that do not belong confuse
            continue
        if re.fullmatch(r"\s*\|?[\s:|-]{3,}\|?\s*", s) and "|" in s:  # table separator row
            continue
        if s.strip().startswith("|") and s.strip().endswith("|"):  # table row -> "a · b · c"
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            s = " · ".join(c for c in cells if c)
        m = re.match(r"^(\s*)#{1,6}\s+(.*?)\s*#*\s*$", s)
        if m:
            s = f"{m.group(1)}*{m.group(2).strip('* ')}*"
        s = re.sub(r"\*\*(.+?)\*\*", r"*\1*", s)
        s = re.sub(r"~~(.+?)~~", r"~\1~", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1: \2", s)
        s = re.sub(r"^(\s*)[•◦▪]\s+", r"\1- ", s)
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))

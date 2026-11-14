"""Learning the owner's dictation / typing fixes from his own edits (owner 10.11, 14.11): shared by the agent (WhatsApp edits)
and the dashboard (editing a message typed there). Pure functions + one append to home/dictionary.md."""
import datetime as dt
import difflib
import os
import re

EDIT_WORD = re.compile(r"^[֐-׿A-Za-z'\"׳״-]{2,}$")


def edit_pairs(old, new):
    """Word swaps between the text as first received and the owner's edit: [(wrong, right)] for 1-3 word
    replacements of real words (no numbers, no punctuation-only, not the same word)."""
    a, b = (old or "").split(), (new or "").split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "replace" or not (1 <= i2 - i1 <= 3 and 1 <= j2 - j1 <= 3):
            continue
        w, r = " ".join(a[i1:i2]).strip(".,!?:;()"), " ".join(b[j1:j2]).strip(".,!?:;()")
        if w != r and all(EDIT_WORD.match(x) for x in (w + " " + r).split()) and len(w) <= 40 and len(r) <= 40:
            out.append((w, r))
    return out


def add_pairs(path, pairs, source="מעריכה"):
    """Append new `wrong → right  # date (source)` lines to the dictionary; returns the lines added (none twice)."""
    if not pairs:
        return []
    have = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    added = [f"{w} → {r}  # {dt.date.today():%d.%m.%Y} ({source})" for w, r in pairs if f"{w} → {r}" not in have]
    if added:
        with open(path, "a", encoding="utf-8") as f:
            f.write(("" if have.endswith("\n") or not have else "\n") + "\n".join(added) + "\n")
    return added

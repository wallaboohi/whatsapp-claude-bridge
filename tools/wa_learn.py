"""Learned preferences of the owner (17.11.2026): behaviour corrections said in free text, kept in agent/home/learned.md.

  add <utf8-file>   append the line(s) of the file (one rule per line, with today's date)
  list              numbered
  remove <n>        moves rule n to learned.removed.md (never deleted)

The bridge passes the newest rules to every run (agent.learned_hints). Word swaps of the dictation go to dictionary.md instead.
"""
import datetime as dt
import os
import sys

HOME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "home")
FILE = os.path.join(HOME, "learned.md")
OLD = os.path.join(HOME, "learned.removed.md")


def rules():
    try:
        return [l.rstrip("\n") for l in open(FILE, encoding="utf-8")]
    except OSError:
        return []


def add(path):
    new = [l.strip().lstrip("-*• ").strip() for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
    have = {l.split("#", 1)[0].strip().lstrip("-*• ").strip() for l in rules()}
    today = dt.date.today().strftime("%d.%m.%Y")
    added = [n for n in new if n.split("#", 1)[0].strip() not in have]
    if added:
        os.makedirs(HOME, exist_ok=True)
        with open(FILE, "a", encoding="utf-8") as f:
            for n in added:
                f.write(f"- {n}  # {today}\n")
    return added


def main(argv):
    if len(argv) >= 3 and argv[1] == "add":
        got = add(argv[2])
        print(f"נשמרו {len(got)} כללים" if got else "כבר קיים, לא נוסף")
    elif len(argv) >= 2 and argv[1] == "list":
        for i, l in enumerate([l for l in rules() if l.strip()], 1):
            print(f"{i}. {l}")
    elif len(argv) >= 3 and argv[1] == "remove":
        ls = [l for l in rules() if l.strip()]
        n = int(argv[2])
        if not 1 <= n <= len(ls):
            sys.exit("אין כלל במספר הזה")
        with open(OLD, "a", encoding="utf-8") as f:
            f.write(ls[n - 1] + "\n")
        open(FILE, "w", encoding="utf-8").write("\n".join(ls[:n - 1] + ls[n:]) + ("\n" if len(ls) > 1 else ""))
        print("הכלל הועבר ל-learned.removed.md")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)

"""Names DB (agent.db `people`, owner 18.11): who is behind a phone / LID.

  uv run -q --no-project python -I -X utf8 tools/people.py find <name|phone|lid>
  uv run -q --no-project python -I -X utf8 tools/people.py nick <phone|lid> <utf8-file with the nickname>
  uv run -q --no-project python -I -X utf8 tools/people.py refresh

Columns: contact_name (owner's phone contacts), push_name (what the person calls himself in WhatsApp), nickname (owner's).
The agent rebuilds contact_name/push_name every 10 minutes; nickname is only set here.
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("WA_AGENT_DB") or os.path.join(ROOT, "agent", "agent.db")


def main(a):
    if not a:
        print(__doc__)
        return 2
    if a[0] == "refresh":
        sys.path.insert(0, os.path.join(ROOT, "agent"))
        os.chdir(os.path.join(ROOT, "agent"))
        import agent  # noqa: E402  (module import does not start the loop)
        agent.people_refresh(force=True)
        print("ok")
        return 0
    with sqlite3.connect(DB, timeout=10) as db:
        if a[0] == "find" and len(a) > 1:
            q = f"%{a[1]}%"
            for r in db.execute("SELECT id, phone, lid, contact_name, push_name, nickname FROM people WHERE id LIKE ? OR "
                                "phone LIKE ? OR lid LIKE ? OR contact_name LIKE ? OR push_name LIKE ? OR nickname LIKE ? "
                                "LIMIT 30", (q,) * 6):
                print(" | ".join(str(x or "") for x in r))
            return 0
        if a[0] == "nick" and len(a) > 2:
            nick = open(a[2], encoding="utf-8").read().strip()
            n = db.execute("UPDATE people SET nickname = ? WHERE id = ? OR phone = ? OR lid = ?", (nick, a[1], a[1], a[1])).rowcount
            print(f"updated {n}")
            return 0 if n else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

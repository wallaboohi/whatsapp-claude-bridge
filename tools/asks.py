"""Owner 19.11.2026 21:14: the open-topics mechanism from the command line (for any AI working for the owner).

    uv run -q --no-project python -I -X utf8 tools/asks.py list [chat]
    uv run -q --no-project python -I -X utf8 tools/asks.py done <id> "<what was done + commit>"
    uv run -q --no-project python -I -X utf8 tools/asks.py not <id> "<why it will not be done>"
    uv run -q --no-project python -I -X utf8 tools/asks.py open <id>
    uv run -q --no-project python -I -X utf8 tools/asks.py sync        (close what commits cite)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))
import asks  # noqa: E402


def main(argv):
    if not argv or argv[0] == "list":
        print(asks.report(argv[1] if len(argv) > 1 else None))
        return 0
    if argv[0] == "sync":
        print(f"closed by commits: {asks.sync_commits()}")
        return 0
    if argv[0] in ("done", "not", "open", "coded", "dup") and len(argv) >= 2:
        ok = asks.close(int(argv[1].lstrip("#")), argv[0], note=" ".join(argv[2:]))
        print(("ok: #" + argv[1] + " -> " + asks.STATUS_HE[argv[0]]) if ok else "not found: #" + argv[1])
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

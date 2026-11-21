"""Owner 21.11 23:10: on the TrueNAS a clean code copy every day, kept 30 days (older ones deleted), and a full image every 7 days."""
import datetime as dt
import importlib.util
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("nas_backup_t", os.path.join(ROOT, "tools", "nas_backup.py"))
nb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nb)


def test_a_clean_code_copy_every_day_kept_thirty_days(tmp_path):
    folder = tmp_path / "code-daily"
    folder.mkdir()
    today = dt.date.today()
    old, kept = today - dt.timedelta(days=31), today - dt.timedelta(days=29)
    for d in (old, kept):
        (folder / f"{d.isoformat()}.zip").write_bytes(b"x")
    (folder / "notes.txt").write_text("not ours", encoding="utf-8")
    note = nb.code_daily(str(tmp_path), today.isoformat())
    assert note.startswith("ok") and "1 older" in note
    names = sorted(os.listdir(folder))
    assert names == sorted([f"{kept.isoformat()}.zip", f"{today.isoformat()}.zip", "notes.txt"]), "only dated zips over 30 days go"
    with zipfile.ZipFile(folder / f"{today.isoformat()}.zip") as z:
        files = z.namelist()
    assert "agent/agent.py" in files and not any(f.startswith("secrets/") for f in files), "only what git keeps"


def test_a_full_image_every_seven_days(tmp_path):
    assert nb.full_due(str(tmp_path)), "none yet: due"
    full = tmp_path / "full"
    (full / (dt.date.today() - dt.timedelta(days=3)).isoformat()).mkdir(parents=True)
    assert not nb.full_due(str(tmp_path))
    (full / (dt.date.today() - dt.timedelta(days=3)).isoformat()).rename(full / (dt.date.today() - dt.timedelta(days=7)).isoformat())
    assert nb.full_due(str(tmp_path))

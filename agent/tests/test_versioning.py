"""tools/versioning.py: the bridge's version rules in one place (owner 14.11)."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import versioning as v  # noqa: E402


def _root(tmp_path, monkeypatch, tool="1.1.1", dash="1.1.1", tags=("1.1.0", "1.1.1")):
    (tmp_path / "dash").mkdir(exist_ok=True)
    (tmp_path / "agent").mkdir(exist_ok=True)
    (tmp_path / "VERSION").write_text(tool + "\n", encoding="utf-8")
    (tmp_path / "dash" / "VERSION").write_text(dash + "\n", encoding="utf-8")
    monkeypatch.setattr(v, "ROOT", str(tmp_path))
    monkeypatch.setattr(v, "_tags", lambda: set(tags))


def test_next_follows_the_law_and_rolls_over_after_ten(tmp_path, monkeypatch):
    """Owner 14.11 13:58: X.Y.1 ... X.Y.10, then X.(Y+1).0; a release may hold many fixes."""
    _root(tmp_path, monkeypatch)
    assert v.current() == "1.1.1" and v.next_version() == "1.1.2"
    _root(tmp_path, monkeypatch, tool="1.1.9", dash="1.1.9", tags=("1.1.9",))
    assert v.next_version() == "1.1.10"
    _root(tmp_path, monkeypatch, tool="1.1.10", dash="1.1.10", tags=("1.1.10",))
    assert v.next_version() == "1.2.0"
    _root(tmp_path, monkeypatch, tool="1.0.12", dash="1.0.15", tags=("1.0.12",))  # numbers above 10 from before the law roll over too
    assert v.next_version() == "1.1.0"


def test_the_law_refuses_everything_but_the_next_and_has_no_exceptions(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert v.check("1.1.2") == ""
    assert "1.1.2" in v.check("1.2.0") and "אין חריגות" in v.check("1.2.0")
    assert "כבר קיבלה תג" in v.check("1.1.0") and "לא גבוהה" in v.check("1.0.9") and "לא תקין" in v.check("1.1")
    assert v.approve("1.2.0") is False and v.check("1.2.0") != ""  # no approval can open an exception
    assert v.check_dash("1.1.1") == "" and "אותו מספר" in v.check_dash("1.0.15")


def test_the_release_scripts_ask_the_gate_before_touching_any_version_file():
    rel = open(os.path.join(ROOT, "tools", "release.py"), encoding="utf-8").read()
    assert "versioning.check(ver)" in rel
    assert rel.index("versioning.check(ver)") < rel.index('for rel in ("VERSION", "dash/VERSION")')
    dash = open(os.path.join(ROOT, "tools", "release_dash.py"), encoding="utf-8").read()
    assert "versioning.check_dash(ver)" in dash
    assert dash.index("check_dash") < dash.index('open(os.path.join(ROOT, "dash", "VERSION"), "w")')


def test_the_nightly_release_takes_its_number_from_the_law():
    src = open(os.path.join(HERE, "agent.py"), encoding="utf-8").read()
    i = src.index("def auto_release():")
    body = src[i:src.index("\ndef ", i + 10)]
    assert "versioning_mod().next_version()" in body and "c + 1" not in body

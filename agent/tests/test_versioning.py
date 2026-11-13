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
    monkeypatch.setattr(v, "APPROVAL", str(tmp_path / "agent" / "version_approval.json"))
    monkeypatch.setattr(v, "_tags", lambda: set(tags))


def test_next_is_one_patch_above_everything_the_project_has_seen(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert v.current() == "1.1.1" and v.next_version() == "1.1.2"
    _root(tmp_path, monkeypatch, tool="1.0.12", dash="1.0.15", tags=("1.0.12",))  # the old dash-only numbers still count
    assert v.next_version() == "1.0.16"


def test_the_rule_refuses_skips_reuse_and_steps_back(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert v.check("1.1.2") == ""
    assert "1.1.2" in v.check("1.2.0") and "/גרסה אשר 1.2.0" in v.check("1.2.0")  # a jump: refused, the way to ask is named
    assert "כבר קיבלה תג" in v.check("1.1.0") and "לא גבוהה" in v.check("1.0.9") and "לא תקין" in v.check("1.1")
    assert v.check_dash("1.1.1") == "" and "אותו מספר" in v.check_dash("1.0.15")  # the app never gets a number of its own


def test_an_approval_is_for_one_release_only(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert not v.approve("1.1.1") and not v.approve("1.1.0")  # not above the current: cannot be approved
    assert v.approve("1.2.0") and v.check("1.2.0") == "" and v.check("1.3.0") != ""
    v.consume("1.2.0")
    assert v.approved() is None and v.check("1.2.0") != ""


def test_the_release_scripts_ask_the_gate_before_touching_any_version_file():
    rel = open(os.path.join(ROOT, "tools", "release.py"), encoding="utf-8").read()
    assert "versioning.check(ver)" in rel
    assert rel.index("versioning.check(ver)") < rel.index('for rel in ("VERSION", "dash/VERSION")')
    dash = open(os.path.join(ROOT, "tools", "release_dash.py"), encoding="utf-8").read()
    assert "versioning.check_dash(ver)" in dash
    assert dash.index("check_dash") < dash.index('open(os.path.join(ROOT, "dash", "VERSION"), "w")')

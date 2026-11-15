"""FAKE_NOW="2026-11-10 02:00" runs the whole suite as if the clock showed that time (time.time / datetime.now / today).
tools/release.py runs the tests at several such times (night, quiet hours, weekend) so a test that passes only at some
hours of the day fails before the nightly release, not during it (owner 10.11: the 23:30 release broke on one)."""
import datetime as _dt
import os
import time as _time

# A temp folder in the user's profile can end up unreadable (broken ACL), which turned every tmp_path test into an
# error and failed releases (15.11). Keep pytest's temp root inside the repo, where the tool always has rights.
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".pytest_tmp"))
os.makedirs(os.environ["PYTEST_DEBUG_TEMPROOT"], exist_ok=True)

_fake = os.environ.get("FAKE_NOW")
if _fake:
    _shift = _dt.datetime.fromisoformat(_fake).timestamp() - _time.time()
    _real_time = _time.time
    _time.time = lambda: _real_time() + _shift

    class _FakeDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(_time.time(), tz)

        @classmethod
        def today(cls):
            return cls.fromtimestamp(_time.time())

    class _FakeDate(_dt.date):
        @classmethod
        def today(cls):
            return cls.fromtimestamp(_time.time())

    _dt.datetime = _FakeDateTime
    _dt.date = _FakeDate

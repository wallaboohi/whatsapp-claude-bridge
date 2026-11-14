import extras


class Response:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        pass

    def json(self):
        return self.value


def test_group_info_reads_live_name_from_bridge(monkeypatch):
    seen = []
    monkeypatch.setattr(extras, "WHATSAPP_API_BASE_URL", "http://bridge/api")
    monkeypatch.setattr(extras, "_auth", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(extras.requests, "post", lambda *a, **k: seen.append((a, k)) or Response({"success": True, "name": "קבוצת בדיקה"}))

    result = extras.group_info("123@g.us")

    assert result == {"success": True, "name": "קבוצת בדיקה"}
    assert seen[0][0][0] == "http://bridge/api/group_info"
    assert seen[0][1]["json"] == {"jid": "123@g.us"}
    assert seen[0][1]["headers"] == {"Authorization": "Bearer test"}


def test_set_group_name_requires_group_and_nonempty_name(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("invalid input must not reach the bridge")

    monkeypatch.setattr(extras.requests, "post", unexpected_request)

    assert extras.set_group_name("123@s.whatsapp.net", "שם")["success"] is False
    assert extras.set_group_name("123@g.us", "  ")["success"] is False
    assert extras.set_group_name("123@g.us", "א" * 101)["success"] is False


def test_set_group_name_updates_bridge(monkeypatch):
    seen = []
    monkeypatch.setattr(extras, "WHATSAPP_API_BASE_URL", "http://bridge/api")
    monkeypatch.setattr(extras.requests, "post", lambda *a, **k: seen.append((a, k)) or Response({"success": True}))

    result = extras.set_group_name("123@g.us", "  שם חדש  ")

    assert result == {"success": True}
    assert seen[0][0][0] == "http://bridge/api/set_group"
    assert seen[0][1]["json"] == {"jid": "123@g.us", "name": "שם חדש"}

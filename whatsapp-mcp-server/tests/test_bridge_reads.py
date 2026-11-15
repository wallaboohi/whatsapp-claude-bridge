import requests

import whatsapp


def test_bridge_read_uses_authenticated_gateway(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"success": True, "rows": [{"id": "m1"}]}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(whatsapp, "_auth", lambda: {"Authorization": "Bearer test-token"})
    monkeypatch.setattr(requests, "post", post)
    assert whatsapp._bridge_read("messages", chat_jid="group@g.us") == [{"id": "m1"}]
    assert seen["url"].endswith("/read")
    assert seen["headers"]["Authorization"] == "Bearer test-token"
    assert seen["json"] == {"kind": "messages", "chat_jid": "group@g.us"}

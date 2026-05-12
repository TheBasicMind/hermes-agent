import json

from tools.all_x_tool import all_x_read


def test_article_get_maps_aisa_article_payload(monkeypatch):
    import agent.integrations.all_x as all_x

    def fake_route_read(action, **kwargs):
        assert action == "article_get"
        assert kwargs["tweet_id"] == "2049805838232047907"
        return {
            "provider": "aisa",
            "action": "article_get",
            "raw": {
                "status": "success",
                "msg": "success",
                "article": {
                    "id": "QXJ0aWNsZUVudGl0eToyMDQ5ODAyODA2ODYxNzIxNjAw",
                    "title": "3 Claude Skills You Can Sell For $1,000 Each",
                    "createdAt": "Thu Apr 30 10:59:30 +0000 2026",
                    "author": {"id": "1644401923787726849", "userName": "Zephyr_hg"},
                    "contents": [
                        {"type": "text", "text": "Most freelancers think Claude Skills are a cute productivity feature."},
                        {"type": "text", "text": "They're packageable services."},
                    ],
                },
            },
        }

    monkeypatch.setattr(all_x, "route_read", fake_route_read)

    payload = json.loads(all_x_read(action="article_get", tweet_id="2049805838232047907"))

    assert payload["success"] is True
    assert payload["provider"] == "aisa"
    result = payload["result"]
    article = result["article"]
    assert article["title"] == "3 Claude Skills You Can Sell For $1,000 Each"
    assert article["body_length"] > 0
    assert "Most freelancers think Claude Skills" in article["body"]
    assert result["debug"]["parse_stage"] == "body_extracted"


def test_article_get_empty_article_returns_structured_error(monkeypatch):
    import agent.integrations.all_x as all_x

    def fake_route_read(action, **kwargs):
        if action == "article_get":
            return {
                "provider": "aisa",
                "action": "article_get",
                "raw": {"status": "success", "msg": "article not found", "article": None},
            }
        if action == "tweet_get":
            return {
                "provider": "x_official",
                "action": "tweet_get",
                "raw": {"data": {"id": kwargs["tweet_id"], "text": "parent tweet exists"}},
            }
        raise AssertionError(action)

    monkeypatch.setattr(all_x, "route_read", fake_route_read)

    payload = json.loads(all_x_read(action="article_get", tweet_id="missing"))

    assert payload["success"] is False
    assert payload["error_class"] == "ARTICLE_NOT_FOUND"
    assert payload["debug"]["aisa"]["provider_attempted"] == "aisa"
    assert payload["debug"]["aisa"]["parse_stage"] == "article_missing"
    assert payload["debug"]["fallback"]["action"] == "tweet_get"
    assert payload["debug"]["fallback"]["reason"] == "x_official has no article body endpoint in all_x"

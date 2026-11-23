"""비슷한 메뉴 안내가 후보 화면까지 전달되는지 (이슈 #37)."""

from tests.orchestrator.conftest import OK_MESSAGE


def test_search_notice_and_match_reach_candidate_prompt(env, monkeypatch):
    original = env.world.discovery.search

    def search(request_id, body):
        found = original(request_id, body)
        found["search_notice"] = "'마라탕'을 파는 가게가 없어 비슷한 메뉴를 골랐어요."
        found["candidates"][0]["match"] = {"level": "SIMILAR_TASTE", "label": "비슷한 맛(매운·국물)"}
        return found

    monkeypatch.setattr(env.world.discovery, "search", search)
    v = env.flow("CANDIDATES_READY", message=OK_MESSAGE)
    prompt = v["prompt"]
    assert prompt["search_notice"] == "'마라탕'을 파는 가게가 없어 비슷한 메뉴를 골랐어요."
    assert prompt["candidates"][0]["match"]["label"] == "비슷한 맛(매운·국물)"


def test_prompt_without_notice_stays_compatible(env):
    v = env.flow("CANDIDATES_READY", message=OK_MESSAGE)
    assert v["prompt"]["search_notice"] is None
    assert v["prompt"]["candidates"][0]["match"] is None

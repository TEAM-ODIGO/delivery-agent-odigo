from app.modules.discovery.extractor import extract_by_rules


def test_spec_example_sentence():
    constraints, notes = extract_by_rules("둘이서 2만원 이하로 안 매운 치킨 빨리 시켜줘")

    assert constraints["people"] == 2
    assert constraints["budget_krw"] == 20000
    assert constraints["cuisine"] == "치킨"
    assert constraints["max_spice_level"] == 0
    assert constraints["max_delivery_minutes"] == 40
    assert notes["max_delivery_minutes"] == "'빨리' → 배달예상 40분 이내 (기본 규칙)"
    assert notes["max_spice_level"] == "'안 매운' → max_spice_level 0"


def test_missing_required_slots_stay_empty():
    constraints, _ = extract_by_rules("안 매운 치킨 빨리 시켜줘")

    assert constraints["people"] is None
    assert constraints["budget_krw"] is None


def test_budget_formats():
    cases = {
        "2만원": 20000,
        "2만5천원": 25000,
        "25,000원": 25000,
        "만오천원": 15000,
        "삼만원": 30000,
        "8천원": 8000,
    }
    for text, expected in cases.items():
        constraints, _ = extract_by_rules(f"혼자 {text} 치킨")
        assert constraints["budget_krw"] == expected, text


def test_per_person_budget_multiplies_people():
    constraints, notes = extract_by_rules("3명 인당 1만원 피자")

    assert constraints["budget_krw"] == 30000
    assert "인당" in notes["budget_krw"]


def test_people_words():
    assert extract_by_rules("혼자 먹을 거")[0]["people"] == 1
    assert extract_by_rules("두 명이 먹을 거")[0]["people"] == 2
    assert extract_by_rules("넷이서 먹을 거")[0]["people"] == 4
    assert extract_by_rules("2인분 주세요")[0]["people"] == 2


def test_excluded_ingredients_and_cuisine_not_confused():
    constraints, _ = extract_by_rules("오이랑 당근 빼고 김밥 주세요. 고기는 빼고")

    assert constraints["excluded_ingredients"] == ["오이", "당근", "고기"]
    assert constraints["cuisine"] == "분식"  # '고기'가 고기/구이로 잡히지 않음
    assert constraints["menu_keywords"] == ["김밥"]


def test_allergy_and_minutes():
    constraints, _ = extract_by_rules("땅콩 알레르기 있어요 30분 안에 중식")

    assert constraints["excluded_ingredients"] == ["땅콩"]
    assert constraints["max_delivery_minutes"] == 30
    assert constraints["cuisine"] == "중식"


def test_local_currency():
    constraints, _ = extract_by_rules("지역화폐로 결제할게")

    assert constraints["payment_requirements"]["local_currency_only"] is True


def test_dish_word_sets_cuisine_and_keyword():
    constraints, notes = extract_by_rules("짜장면 먹고 싶어")

    assert constraints["cuisine"] == "중식"
    assert "짜장" in constraints["menu_keywords"]
    assert notes["cuisine"] == "'짜장면' → cuisine 중식"

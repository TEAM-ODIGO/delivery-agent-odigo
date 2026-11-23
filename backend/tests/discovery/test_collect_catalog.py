"""식당 종류 다양화 수집 (이슈 #58): 적은 종류부터 고르기, 새 줄 만들기, 메뉴 자동 분류, 새 줄만 더하기."""

import csv
from collections import Counter

from sqlalchemy.orm import sessionmaker

from app.core.db import init_db, make_engine
from app.modules.discovery import loader
from app.modules.discovery.catalog_models import Menu, Restaurant
from scripts import catalog_classify as cls
from scripts import collect_catalog as cc
from scripts import recollect_catalog as rc
from tests.discovery.test_recollect import M_FIELDS, R_FIELDS, menu_row, rest_row, write_csv

NOW = "2026-11-23 13:00:00+09"


def item(rid, cats, reviews=100, **over):
    return {"id": rid, "name": f"가게{rid}", "vertical_type": "FOOD", "categories": cats, "rating": 4.8,
            "review_count": reviews, "lat": 37.5651799, "lng": 126.9905697, "available": True,
            "eta_min": 25, "eta_max": 40} | over


# ---------------------------------------------------------------- 고르기
def test_candidates_filter_and_rank():
    lists = {
        "pig_trotter": [item("J1", ["족발/보쌈", "포장"]), item("J2", ["족발/보쌈"], reviews=3), item("OLD", ["족발/보쌈"])],
        "late_night_snack": [item("B1", ["버거"]), item("J1", ["족발/보쌈", "포장"])],
        "franchise": [item("X", ["편의점"], vertical_type="FOOD"), item("Y", ["버거"], available=False),
                      item("Z", ["버거"], vertical_type="YOSTORE")],
    }
    pool = cc.candidates(lists, {"OLD"}, min_reviews=10)
    assert [p["id"] for p in pool] == ["J1", "B1"]  # 리뷰 적음·기존·배달 불가·음식점 아님은 뺀다
    assert pool[0]["slugs"] == ["pig_trotter", "late_night_snack"]


def test_plan_fills_scarce_types_first_and_skips_full_ones():
    existing = Counter({t: 20 for t in cc.TYPES}) | Counter({"족발/보쌈": 18, "버거": 19})
    existing["족발/보쌈"], existing["버거"] = 18, 19
    pool = [item("P1", ["피자/양식"]), item("J1", ["족발/보쌈", "고기/구이"]), item("B1", ["버거"]),
            item("J2", ["족발/보쌈"]), item("J3", ["족발/보쌈"]), item("B2", ["버거"])]
    chosen, after = cc.plan(existing, [p | {"rank": i} for i, p in enumerate(pool)], target=20, max_new=50)
    assert [c["id"] for c in chosen] == ["J1", "B1", "J2"]  # 피자는 이미 충분해서 고르지 않는다
    assert after["족발/보쌈"] == 20 and after["버거"] == 20 and after["고기/구이"] == 21
    assert chosen[0]["for"] == "족발/보쌈"


def test_plan_stops_when_type_runs_out():
    chosen, after = cc.plan(Counter(), [item("J1", ["족발/보쌈"]) | {"rank": 0}], target=3, max_new=50)
    assert len(chosen) == 1 and after["족발/보쌈"] == 1  # 더 없으면 그 종류는 멈추고 끝낸다


# ---------------------------------------------------------------- 메뉴 분류
def test_menu_type_rules():
    assert cls.menu_type("족발 대", "메인 메뉴", False, 39000) == "main"
    assert cls.menu_type("콜라 1.25L", "음료", False, 3000) == "drink"
    assert cls.menu_type("제로콜라", "추가 메뉴", False, 2500) == "drink"
    assert cls.menu_type("공기밥", "추가 메뉴", False, 1000) == "side"
    assert cls.menu_type("치즈볼 5개", "치킨", False, 5000) == "side"
    assert cls.menu_type("혼밥 족발 세트", "세트메뉴", False, 15900) == "set"
    assert cls.menu_type("참이슬", "주류", True, 5000) == "alcohol"
    assert cls.menu_type("사케동", "덮밥", False, 14000) == "main"  # '사케'로 술 취급하지 않는다


def test_taste_and_servings_only_when_clear():
    assert cls.spice_level("불족발", "") == 2 and cls.spice_level("엽기떡볶이", "") == 3
    assert cls.spice_level("안매운 닭볶음탕", "") == 0 and cls.spice_level("족발", "") is None
    assert cls.has_soup("순대국밥") is True and cls.has_soup("짜장면") is False and cls.has_soup("족발") is False
    assert cls.has_soup("모둠") is None
    assert cls.servings("족발 (2~3인분)", "") == 2.0 and cls.servings("보쌈", "1인용 구성") == 1.0
    assert cls.servings("족발", "") is None


def test_classify_restaurant_marks_signature_and_popular():
    menus = [{"name": f"메뉴{i}", "price": 10000, "review_count": i * 10, "section": "메인"} for i in range(5)]
    menus.append({"name": "콜라", "price": 2000, "review_count": 999, "section": "음료"})
    menus[0]["signature"] = True
    out = cls.classify_restaurant(menus)
    assert out[0]["대표메뉴"] == "t"
    assert [o["인기"] for o in out] == ["f", "f", "t", "t", "t", "f"]  # 음료는 인기 본메뉴에서 뺀다


# ---------------------------------------------------------------- 새 줄 만들기·쓰기
def page_for(rid):
    return {
        "name": f"가게{rid}", "min_order": 18000, "delivery_fee": 3000,
        "tiers": [{"order_amount": 18000, "delivery_fee": 3000}], "hours": "11:00 ~ 23:00",
        "rating": 4.9, "review_count": 200, "voucher": True, "address": "서울특별시 중구  저동2가 7-2",
        "lat": 37.5651799, "lng": 126.9905697, "categories": ["족발/보쌈", "포장"],
        "sections": [{"id": "C", "type": "CURATION", "title": "대표메뉴", "items": ["M1"]},
                     {"id": "S1", "type": "LIST", "title": "족발", "items": ["M1", "M2"]},
                     {"id": "S2", "type": "LIST", "title": "음료", "items": ["M3"]}],
        "menus": [
            {"id": "M1", "section_id": "S1", "name": "불족발 (2~3인분)", "description": "", "soldout": False,
             "liquor": False, "review_count": 30, "origin_price": 39000, "final_price": 36000,
             "final_price_with_required_options": 36000, "required": False},
            {"id": "M2", "section_id": "S1", "name": "보쌈", "description": "", "soldout": True,
             "liquor": False, "review_count": 3, "origin_price": 35000, "final_price": 35000,
             "final_price_with_required_options": 35000, "required": True},
            {"id": "M3", "section_id": "S2", "name": "콜라", "description": "", "soldout": False,
             "liquor": False, "review_count": 0, "origin_price": 2000, "final_price": 2000,
             "final_price_with_required_options": 2000, "required": False},
        ],
    }


def test_build_rows():
    chosen = [item("J1", ["족발/보쌈", "포장"]) | {"slugs": ["pig_trotter"], "for": "족발/보쌈"},
              item("J9", ["족발/보쌈"]) | {"slugs": ["pig_trotter"], "for": "족발/보쌈"}]
    new = cc.build(chosen, {"J1": page_for("J1"), "J9": {"error": "막힘"}}, NOW)
    r = new["restaurants"][0]
    assert r["요기요카테고리"] == "족발/보쌈,포장" and r["수집카테고리"] == "pig_trotter"
    assert r["최소주문금액"] == "18000" and r["예상배달분"] == "25~40" and r["지역화폐가능"] == "t"
    assert r["주소"] == "서울특별시 중구 저동2가 7-2" and r["메뉴수"] == "3"
    assert 600 < int(r["거리m"]) < 800  # 을지로입구역에서 약 700m
    assert r["보정별점"] == f"{(4.9 * 200 + 4.78 * 49) / 249:.3f}"
    m1, m2, m3 = new["menus"]
    assert (m1["유형"], m1["맵기"], m1["인분"], m1["대표메뉴"], m1["정가"], m1["판매가"]) == ("main", "2", "2.0", "t", "39000", "36000")
    assert m2["품절"] == "t" and m2["필수옵션있음"] == "t"
    assert m3["유형"] == "drink" and m3["맵기"] == ""
    assert new["unread"] == [{"restaurant_id": "J9", "name": "가게J9", "reason": "막힘"}]


def test_write_appends_only_new_rows_and_db_picks_them_up(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    rpath, mpath = data / "1_식당.csv", data / "2_메뉴.csv"
    write_csv(rpath, R_FIELDS, [rest_row("R1", "치킨집")])
    write_csv(mpath, M_FIELDS, [menu_row("R1", "치킨집", "M0", "후라이드", 18000)])
    before_r, before_m = rpath.read_text(encoding="utf-8-sig"), mpath.read_text(encoding="utf-8-sig")
    monkeypatch.setattr(rc, "RESTAURANT_CSV", rpath)
    monkeypatch.setattr(rc, "MENU_CSV", mpath)
    monkeypatch.setattr(cc, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(loader, "DATA_DIR", data)

    engine = make_engine(f"sqlite:///{tmp_path / 'c.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as s:
        loader.load_if_empty(s)
        s.commit()

    chosen = [item("J1", ["족발/보쌈"]) | {"slugs": ["pig_trotter"], "for": "족발/보쌈"},
              item("R1", ["치킨"]) | {"slugs": ["chicken"], "for": "치킨"}]
    new = cc.build(chosen, {"J1": page_for("J1"), "R1": page_for("R1")}, NOW)
    assert cc.write(new) == (1, 3)  # 이미 있는 R1은 건너뛴다
    assert rpath.read_text(encoding="utf-8-sig").startswith(before_r)  # 기존 줄은 글자 그대로
    assert mpath.read_text(encoding="utf-8-sig").startswith(before_m)
    assert list(csv.DictReader(rpath.open(encoding="utf-8-sig")))[-1]["식당id"] == "J1"
    assert any((tmp_path / "runs").glob("catalog-backup-*/1_식당.csv"))
    assert cc.type_counts(rc.restaurant_rows())["족발/보쌈"] == 1

    with sessions() as s:
        assert loader.sync_if_newer(s) is not None
        assert s.get(Restaurant, "J1").opening_hours == "11:00 ~ 23:00"
        assert s.get(Menu, "M1").menu_type == "main" and s.get(Menu, "M3").menu_type == "drink"
    engine.dispose()


def test_report_lists_counts_and_menus():
    chosen = [item("J1", ["족발/보쌈"]) | {"slugs": ["pig_trotter"], "for": "족발/보쌈"}]
    new = cc.build(chosen, {"J1": page_for("J1")}, NOW)
    text = cc.report(new, Counter({"족발/보쌈": 2}))
    assert "| 족발/보쌈 | 2 | 3 |" in text
    assert "[main] 불족발 (2~3인분) 36,000원" in text


def test_cafe_drinks_and_big_side_items():
    assert cls.menu_type("얼음없이딸기라떼", "라떼는말이야 라떼~ 메뉴", False, 10500) == "drink"
    assert cls.menu_type("[박수박수] 수박주스", "SIGNATURE 메뉴", False, 6500) == "drink"
    assert cls.menu_type("고구마라떼케이크(15cm)", "케이크", False, 35000) == "main"  # 이름에 라떼가 있어도 케이크
    assert cls.menu_type("매콤직화불족발 [반]", "사이드 메뉴", False, 22000) == "main"
    assert cls.menu_type("족발추가(반족)", "추가메뉴", False, 28000) == "side"
    assert cls.menu_type("PARTY머리띠（핑크）", "파티용품", False, 4800) == "side"  # 음식이 아닌 물건
    assert cls.menu_type("민트 초콜릿 칩 레디팩", "레디팩", False, 12900) == "main"


def test_reclassify_updates_saved_labels():
    chosen = [item("J1", ["족발/보쌈"]) | {"slugs": ["pig_trotter"], "for": "족발/보쌈"}]
    new = cc.build(chosen, {"J1": page_for("J1")}, NOW)
    new["menus"][2]["유형"] = "main"  # 예전 규칙으로 잘못 붙은 값
    assert cc.reclassify(new)["menus"][2]["유형"] == "drink"
    assert new["menus"][0]["대표메뉴"] == "t"  # 대표메뉴는 저장된 값을 그대로 쓴다


def test_add_forces_named_stores_even_when_type_is_full():
    lists = {"cafe_dessert": [item("BR1", ["카페/디저트"], reviews=5, name="배스킨라빈스-광화문점"),
                              item("PB", ["카페/디저트"], name="파리바게뜨")]}
    forced = cc.forced_picks(lists, set(), ["배스킨라빈스"])
    assert [f["id"] for f in forced] == ["BR1"]  # 리뷰가 적어도 직접 고른 가게는 넣는다
    full = Counter({t: 30 for t in cc.TYPES})
    chosen, after = cc.plan(full, cc.candidates(lists, set(), 10), 20, 50, forced)
    assert [c["id"] for c in chosen] == ["BR1"] and chosen[0]["for"] == "직접 추가"
    assert after["카페/디저트"] == 31


def test_plan_prefers_new_brands_and_per_type_targets():
    pool = [item("A1", ["카페/디저트"], name="뚜레쥬르-무교점"), item("A2", ["카페/디저트"], name="뚜레쥬르-시청점"),
            item("A3", ["카페/디저트"], name="요아정-을지로점"), item("A4", ["카페/디저트"], name="던킨-숭례문점")]
    full = Counter({t: 30 for t in cc.TYPES}) | Counter()
    full["카페/디저트"] = 0
    chosen, _ = cc.plan(full, [p | {"rank": i} for i, p in enumerate(pool)], 20, 50,
                        targets={"카페/디저트": 3}, existing_brands={"뚜레쥬르"})
    assert [c["id"] for c in chosen] == ["A3", "A4", "A1"]  # 없는 브랜드 먼저, 모자라면 있는 브랜드

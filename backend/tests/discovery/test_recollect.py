"""식당·메뉴 재수집 (이슈 #35): 바뀐 점 비교, 안전하게 쓰기, 서버 재시작 때 DB 반영."""

import csv
import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.db import init_db, make_engine
from app.modules.discovery import loader
from app.modules.discovery.catalog_models import Menu, Restaurant
from scripts import recollect_catalog as rc

R_FIELDS = [
    "식당id",
    "식당명",
    "수집카테고리",
    "요기요카테고리",
    "별점",
    "리뷰수",
    "보정별점",
    "최소주문금액",
    "기본배달비",
    "주문금액별배달비",
    "예상배달분",
    "거리m",
    "지역화폐가능",
    "메뉴수",
    "주소",
    "요기요링크",
    "수집시각",
    "영업시간",
]
M_FIELDS = [
    "식당id",
    "식당명",
    "메뉴분류",
    "메뉴id",
    "메뉴명",
    "설명",
    "정가",
    "판매가",
    "필수옵션포함가",
    "필수옵션있음",
    "유형",
    "맵기",
    "국물",
    "인분",
    "대표메뉴",
    "인기",
    "품절",
    "주류",
    "메뉴리뷰수",
]
OLD_AT = "2026-11-11 12:38:18.287+09"
NEW_AT = "2026-11-21 11:50:00+09"


def rest_row(rid, name, **over):
    row = {
        "식당id": rid,
        "식당명": name,
        "수집카테고리": "chicken",
        "요기요카테고리": "치킨",
        "별점": "4.9",
        "리뷰수": "100",
        "보정별점": "4.9",
        "최소주문금액": "15000",
        "기본배달비": "2000",
        "주문금액별배달비": '[{"delivery_fee": 2000, "order_amount": 15000}]',
        "예상배달분": "20~30",
        "거리m": "500",
        "지역화폐가능": "t",
        "메뉴수": "3",
        "주소": "서울특별시 중구",
        "요기요링크": f"https://www.yogiyo.co.kr/mobile/#/{rid}/",
        "수집시각": OLD_AT,
        "영업시간": "11:00 ~ 22:00",
    }
    return row | over


def menu_row(rid, name, mid, menu_name, price, **over):
    row = {
        "식당id": rid,
        "식당명": name,
        "메뉴분류": "치킨",
        "메뉴id": mid,
        "메뉴명": menu_name,
        "설명": "",
        "정가": str(price),
        "판매가": str(price),
        "필수옵션포함가": str(price),
        "필수옵션있음": "f",
        "유형": "main",
        "맵기": "2",
        "국물": "",
        "인분": "1.0",
        "대표메뉴": "t",
        "인기": "f",
        "품절": "f",
        "주류": "f",
        "메뉴리뷰수": "3",
    }
    return row | over


def yo_menu(mid, name, price, section="S1", soldout=False, required=False):
    return {
        "id": mid,
        "section_id": section,
        "name": name,
        "description": "",
        "soldout": soldout,
        "liquor": False,
        "review_count": 5,
        "origin_price": price,
        "final_price": price,
        "final_price_with_required_options": price,
        "required": required,
    }


def observed_for_r1(**over):
    obs = {
        "min_order": 16000,
        "delivery_fee": 2000,
        "tiers": [{"order_amount": 16000, "delivery_fee": 2000}],
        "hours": "11:00 ~ 23:00",
        "status": "OPENING_TIME",
        "rating": 4.8,
        "review_count": 120,
        "voucher": True,
        "sections": [
            {"id": "S1", "type": "LIST", "title": "치킨 메뉴", "items": ["M1", "M2", "M9", "M5"]}
        ],
        "menus": [
            yo_menu("M1", "후라이드", 19000),  # 가격 오름
            yo_menu("M2", "양념", 20000, soldout=True),  # 품절됨
            # M3 매운치킨은 사라짐
            yo_menu("M9", "간장", 20000),  # M4 간장이 새 번호로
            yo_menu("M5", "신메뉴 마늘치킨", 21000),  # 새 메뉴
        ],
    }
    return obs | over


def sample():
    rests = [rest_row("R1", "치킨집"), rest_row("R2", "피자집")]
    menus = [
        menu_row("R1", "치킨집", "M1", "후라이드", 18000),
        menu_row("R1", "치킨집", "M2", "양념", 20000),
        menu_row("R1", "치킨집", "M3", "매운치킨", 20000),
        menu_row("R1", "치킨집", "M4", "간장", 20000),
        menu_row("R2", "피자집", "P1", "치즈피자", 22000),
    ]
    return rests, menus


# ---------------------------------------------------------------- 비교·합치기
def test_merge_updates_prices_soldout_and_keeps_classification():
    rests, menus = sample()
    report = rc.merge(
        rests,
        menus,
        {"R1": observed_for_r1(), "R2": {"error": "식당 페이지가 열리지 않음"}},
        NEW_AT,
    )
    m = {row["메뉴id"]: row for row in menus}

    assert m["M1"]["판매가"] == "19000" and m["M1"]["필수옵션포함가"] == "19000"
    assert m["M1"]["메뉴분류"] == "치킨 메뉴"
    assert (m["M1"]["유형"], m["M1"]["맵기"], m["M1"]["인분"], m["M1"]["대표메뉴"]) == (
        "main",
        "2",
        "1.0",
        "t",
    )
    assert m["M2"]["품절"] == "t"
    assert m["M3"]["품절"] == "t"  # 사라진 메뉴는 지우지 않고 품절
    assert (
        "M4" not in m and m["M9"]["메뉴명"] == "간장" and m["M9"]["유형"] == "main"
    )  # 번호만 바뀜
    assert "M5" not in m  # 새 메뉴는 넣지 않음
    assert len(menus) == 5

    r1 = report["restaurants"][0]
    assert [x["name"] for x in r1["price"]] == ["후라이드"] and (
        r1["price"][0]["old"],
        r1["price"][0]["new"],
    ) == (18000, 19000)
    assert [x["name"] for x in r1["soldout"]] == ["양념"]
    assert [x["name"] for x in r1["removed"]] == ["매운치킨"]
    assert r1["new_id"] == [{"name": "간장", "old_id": "M4", "new_id": "M9"}]
    assert [x["name"] for x in r1["new"]] == ["신메뉴 마늘치킨"]
    assert {f["field"] for f in r1["fields"]} == {"최소주문금액", "주문금액별배달비", "영업시간"}


def test_merge_updates_restaurant_and_leaves_unread_untouched():
    rests, menus = sample()
    before_r2 = dict(rests[1])
    report = rc.merge(rests, menus, {"R1": observed_for_r1(), "R2": {"error": "x"}}, NEW_AT)
    r1, r2 = rests
    assert (r1["최소주문금액"], r1["영업시간"], r1["별점"], r1["리뷰수"], r1["메뉴수"]) == (
        "16000",
        "11:00 ~ 23:00",
        "4.8",
        "120",
        "4",
    )
    assert json.loads(r1["주문금액별배달비"]) == [{"delivery_fee": 2000, "order_amount": 16000}]
    assert r1["수집시각"] == NEW_AT
    assert r2 == before_r2  # 못 읽은 식당은 그대로
    assert next(row for row in menus if row["메뉴id"] == "P1")["품절"] == "f"
    assert (report["read"], report["total"], report["ratio"]) == (1, 2, 0.5)
    assert report["unread"] == [{"restaurant_id": "R2", "name": "피자집", "reason": "x"}]


def test_same_tiers_in_other_order_is_not_a_change():
    rests, menus = sample()
    rests[0]["주문금액별배달비"] = (
        '[{"delivery_fee": 0, "order_amount": 50000}, '
        '{"delivery_fee": 2000, "order_amount": 16000}]'
    )
    tiers = [
        {"order_amount": 16000, "delivery_fee": 2000},
        {"order_amount": 50000, "delivery_fee": 0},
    ]
    report = rc.merge(rests, menus, {"R1": observed_for_r1(tiers=tiers)}, NEW_AT)
    assert "주문금액별배달비" not in {f["field"] for f in report["restaurants"][0]["fields"]}


def test_fullwidth_and_spaces_are_normalized_like_first_collection():
    rests, menus = sample()
    obs = observed_for_r1()
    obs["menus"][0] = yo_menu("M1", "후라이드（순살）＋콜라", 19000) | {
        "description": " 바삭\n  치킨 "
    }
    report = rc.merge(rests, menus, {"R1": obs}, NEW_AT)
    m1 = next(row for row in menus if row["메뉴id"] == "M1")
    assert (m1["메뉴명"], m1["설명"]) == ("후라이드(순살)+콜라", "바삭 치킨")
    assert [x["new"] for x in report["restaurants"][0]["renamed"]] == ["후라이드(순살)+콜라"]
    menus[0]["메뉴명"] = "후라이드(순살)+콜라"
    report = rc.merge(rests, menus, {"R1": obs}, NEW_AT)
    assert report["restaurants"][0]["renamed"] == []  # 모양만 다르면 바뀐 것이 아님


def test_from_latest_picks_newest_and_missing_file_lists_files(tmp_path, capsys):
    assert rc.find_saved("latest", tmp_path) is None
    (tmp_path / "recollect-20261121-1154.json").write_text("{}")
    (tmp_path / "recollect-20261121-1155.json").write_text("{}")
    assert rc.find_saved("latest", tmp_path).name == "recollect-20261121-1155.json"
    assert rc.find_saved(str(tmp_path / "recollect-날짜.json"), tmp_path) is None
    assert "recollect-20261121-1155.json" in capsys.readouterr().out


def test_half_missing_menus_count_as_unread():
    old = [menu_row("R1", "치킨집", f"M{i}", f"메뉴{i}", 10000) for i in range(10)]
    assert rc.usable(old, {"menus": [yo_menu("M1", "메뉴1", 10000)] * 4}) is not None
    assert rc.usable(old, {"menus": [yo_menu("M1", "메뉴1", 10000)] * 5}) is None
    assert rc.usable(old, {"menus": []}) == "메뉴가 하나도 없음"
    assert rc.usable(old, None) == "결과 없음"


# ---------------------------------------------------------------- CSV 쓰기
def write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_unchanged_lines_are_written_back_as_is(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        '﻿식당id,메뉴명,설명,판매가\nR1,후라이드,"",18000\nR1,양념,"여러 줄\n설명, 쉼표",20000\n',
        encoding="utf-8",
    )
    fields, rows = rc._read_rows(path)
    assert rows[1]["설명"] == "여러 줄\n설명, 쉼표"
    rows[0]["판매가"] = "19000"
    rc._write_rows(path, fields, rows)
    text = path.read_text(encoding="utf-8-sig")
    assert (
        text
        == '식당id,메뉴명,설명,판매가\nR1,후라이드,,19000\nR1,양념,"여러 줄\n설명, 쉼표",20000\n'
    )


def _use_tmp_csv(tmp_path, monkeypatch):
    rests, menus = sample()
    rpath, mpath = tmp_path / "1_식당.csv", tmp_path / "2_메뉴.csv"
    write_csv(rpath, R_FIELDS, rests)
    write_csv(mpath, M_FIELDS, menus)
    monkeypatch.setattr(rc, "RESTAURANT_CSV", rpath)
    monkeypatch.setattr(rc, "MENU_CSV", mpath)
    return rpath, mpath


def test_apply_refuses_to_write_when_too_few_read(tmp_path, monkeypatch):
    rpath, mpath = _use_tmp_csv(tmp_path, monkeypatch)
    before = (rpath.read_bytes(), mpath.read_bytes())
    out = tmp_path / "runs"
    out.mkdir()
    code = rc.apply(
        {"R1": observed_for_r1(), "R2": {"error": "x"}}, NEW_AT, out, write=True, min_ratio=0.8
    )
    assert code == 2
    assert (rpath.read_bytes(), mpath.read_bytes()) == before
    assert list(out.glob("recollect-*-report.md"))  # 보고서는 남김


def test_apply_writes_and_backs_up(tmp_path, monkeypatch):
    rpath, mpath = _use_tmp_csv(tmp_path, monkeypatch)
    original = mpath.read_bytes()
    out = tmp_path / "runs"
    out.mkdir()
    r2 = {"menus": [yo_menu("P1", "치즈피자", 22000)], "sections": []}
    assert (
        rc.apply({"R1": observed_for_r1(), "R2": r2}, NEW_AT, out, write=True, min_ratio=0.8) == 0
    )
    _, menus = rc._read_rows(mpath)
    assert next(r for r in menus if r["메뉴id"] == "M1")["판매가"] == "19000"
    backup = next(out.glob("catalog-backup-*"))
    assert (backup / "2_메뉴.csv").read_bytes() == original
    report = next(out.glob("recollect-*-report.md")).read_text(encoding="utf-8")
    assert "후라이드: 18,000원 → 19,000원" in report and "신메뉴 마늘치킨" in report


def test_preview_does_not_write(tmp_path, monkeypatch):
    rpath, mpath = _use_tmp_csv(tmp_path, monkeypatch)
    before = mpath.read_bytes()
    out = tmp_path / "runs"
    out.mkdir()
    r2 = {"menus": [yo_menu("P1", "치즈피자", 22000)], "sections": []}
    assert (
        rc.apply({"R1": observed_for_r1(), "R2": r2}, NEW_AT, out, write=False, min_ratio=0.8) == 0
    )
    assert mpath.read_bytes() == before


# ---------------------------------------------------------------- 서버 재시작 때 DB 반영
def test_server_start_syncs_db_when_csv_is_newer(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    rests, menus = sample()
    write_csv(data / "1_식당.csv", R_FIELDS, rests)
    write_csv(data / "2_메뉴.csv", M_FIELDS, menus)
    monkeypatch.setattr(loader, "DATA_DIR", data)
    engine = make_engine(f"sqlite:///{tmp_path / 'sync.db'}")
    init_db(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as s:
        loader.load_if_empty(s)
        s.get(Restaurant, "R1").local_currency_merchant = False  # C-08이 고친 값
        s.commit()
        assert loader.sync_if_newer(s) is None  # 같은 수집본이면 아무것도 안 함

    # 재수집: R1 가격·최소주문 바뀜, M4가 M9로 바뀜
    rc.merge(rests, menus, {"R1": observed_for_r1(), "R2": {"error": "x"}}, NEW_AT)
    write_csv(data / "1_식당.csv", R_FIELDS, rests)
    write_csv(data / "2_메뉴.csv", M_FIELDS, menus)
    with sessions() as s:
        assert loader.sync_if_newer(s) == (2, 5, 1)
        r1 = s.get(Restaurant, "R1")
        assert r1.min_order_krw == 16000 and r1.opening_hours == "11:00 ~ 23:00"
        assert r1.local_currency_merchant is False  # C-08 값은 그대로
        assert s.get(Menu, "M1").sale_price_krw == 19000
        assert s.get(Menu, "M3").is_sold_out is True
        assert s.get(Menu, "M9").name == "간장"
        assert s.get(Menu, "M4").is_sold_out is True  # 옛 번호 줄은 품절로
        assert len(s.scalars(select(Menu)).all()) == 6
        assert r1.collected_at > s.get(Restaurant, "R2").collected_at - timedelta(seconds=1)
        assert loader.sync_if_newer(s) is None  # 한 번 맞춘 뒤에는 다시 안 함
    engine.dispose()

"""수집 데이터(CSV)로 임시 DB를 만들어 대표 문장의 추천 결과를 본다 (이슈 #58, 요기요 접속 없음).

    uv run python scripts/check_queries.py              # data/ 기준, 지금 시각
    uv run python scripts/check_queries.py --hour 23    # 밤 11시 기준 (영업시간 필터)
    uv run python scripts/check_queries.py "혼자 베라 2만원" "둘이서 족발 4만원"   # 문장 직접

서버 DB(dev.db)는 건드리지 않는다.
"""
import os, sys, tempfile
from pathlib import Path
import argparse
ap = argparse.ArgumentParser(description="수집 데이터로 대표 문장 추천 결과 보기")
ap.add_argument("messages", nargs="*")
ap.add_argument("--data", default=str(Path(__file__).resolve().parents[1] / "data"))
ap.add_argument("--hour", type=int, default=None, help="이 시각(0~23시) 기준으로 영업 중인 가게만")
ap.add_argument("--top", type=int, default=5, help="후보 수 (화면에 보이는 수, 이슈 #109)")
args = ap.parse_args()
data = Path(args.data)
os.environ["CATALOG_DATA_DIR"] = str(data)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.core.db import init_db, make_engine
from app.modules.discovery import loader
loader.DATA_DIR = data
from app.modules.discovery.brands import find_brands
from app.modules.discovery.catalog_models import Restaurant
from app.modules.discovery.extractor import extract_by_rules
from app.modules.discovery.search import search
from app.modules.discovery import choices
from app.modules.discovery.drinks import search_prefs
from app.modules.discovery.timeutil import KST
db = Path(tempfile.mkdtemp()) / "v.db"
engine = make_engine(f"sqlite:///{db}"); init_db(engine)
S = sessionmaker(bind=engine, expire_on_commit=False)
with S() as s:
    loader.load_if_empty(s); s.commit()
from app.modules.discovery.timeutil import now_kst
now = now_kst()
when = now.replace(hour=args.hour, minute=0) if args.hour is not None else now
msgs = ["혼자 맘스터치 만원 이하", "혼자 베라 2만원 이하", "둘이서 교촌 3만원", "혼자 요아정 1만5천원", "둘이서 마카롱 2만원",
        "혼자 크로플 1만5천원", "둘이서 아이스크림 2만원", "둘이서 족발 4만원", "혼자 쌀국수 1만5천원", "혼자 버거 1만원 이하",
        "둘이서 엽떡 3만원", "혼자 케이크 3만원", "혼자 설빙 2만원"]
msgs = args.messages or msgs
with S() as s:
    names = list(s.scalars(select(Restaurant.name)))
    for m in msgs:
        c, _ = extract_by_rules(m)
        c["brands"] = find_brands(m, names)
        # 서버(search_request)처럼 탐색할 때만 쓰는 값도 넣는다: 음료 선호(#107), 맛 방향(#117)
        prefs = search_prefs(c, m)
        if prefs:
            c["drink_prefs"] = prefs
        c.update(choices.search_prefs(c, m))
        if not c.get("people"): c["people"] = 1
        bodies, _ = search(s, c, args.top, now=when)
        print(f"\n▶ {m}  (브랜드={c['brands']} 종류={c.get('cuisine')} 메뉴={c.get('menu_keywords')} 맛={c.get('taste_preferences')}{' 방향=' + str(c['taste_groups']) if c.get('taste_groups') else ''})")
        if bodies and bodies[0].get("search_notice"): print("   안내:", bodies[0]["search_notice"])
        for b in bodies:
            items = ", ".join(f"{i['name']}×{i['quantity']}" for i in b["items"])
            ok = "조건 충족" if b.get("meets_all_constraints") else "조건 일부 벗어남"
            print(f"   - {b['restaurant']['name']} | {items} | {b.get('match',{}).get('level','EXACT')} | {ok}")
        if not bodies: print("   (후보 없음)")

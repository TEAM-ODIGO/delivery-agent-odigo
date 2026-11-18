"""식당·메뉴 CSV 적재: 한글 파일명이 깨져(%ec%8b%9d…) 풀려도 찾고, 못 찾으면 로그를 남긴다."""

import logging
import shutil
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import init_db, make_engine
from app.modules.discovery import loader
from app.modules.discovery.catalog_models import Restaurant

REAL_DATA = Path(loader.__file__).resolve().parents[3] / "data"


def _empty_session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'loader.db'}")
    init_db(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_loads_csv_even_when_korean_file_names_are_percent_encoded(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    for name in (loader.RESTAURANT_CSV, loader.MENU_CSV):
        shutil.copy(REAL_DATA / name, data / quote(name, safe="").lower())  # 1_%ec%8b%9d%eb%8b%b9.csv
    monkeypatch.setattr(loader, "DATA_DIR", data)
    engine, sessions = _empty_session(tmp_path)
    with sessions() as s:
        assert loader.load_if_empty(s) is not None
        assert s.scalar(select(func.count()).select_from(Restaurant)) > 100
    engine.dispose()


def test_missing_csv_is_logged_not_silent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path / "nowhere")
    engine, sessions = _empty_session(tmp_path)
    with sessions() as s, caplog.at_level(logging.WARNING, logger="app"):
        assert loader.load_if_empty(s) is None
    assert "CSV를 찾지 못해" in caplog.text
    engine.dispose()

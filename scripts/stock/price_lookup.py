"""국내/해외 시세 조회 + 국내 종목명→코드 자동 해석.

비공식 엔드포인트를 쓴다 (2026-09 기준 실제 호출로 확인한 응답 형식):
  - 국내 시세: m.stock.naver.com/api/stock/{code}/basic
  - 국내 일별 시세(시가 포함): m.stock.naver.com/api/stock/{code}/price
  - 국내 종목 검색(이름→코드): ac.stock.naver.com/ac
  - 해외 시세: query1.finance.yahoo.com/v8/finance/chart/{ticker}

네이버/야후가 응답 형식을 바꾸면 이 모듈도 함께 손봐야 한다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import NamedTuple

import requests

_UA = "Mozilla/5.0 (compatible; stock-record-bot/1.0)"


class Quote(NamedTuple):
    price: float
    prev_close: float
    pct_change: float  # 전일 종가 대비 등락률 (%), 부호 포함


def is_overseas_ticker(token: str) -> bool:
    """토큰이 영문(ASCII 알파벳/점)만으로 구성되면 해외 티커로 간주한다."""
    return bool(re.fullmatch(r"[A-Za-z.]+", token))


def resolve_kr_code(name: str, ticker_map_path: Path) -> str:
    """국내 종목명을 종목코드로 변환한다. 로컬 캐시 파일을 먼저 보고,
    없으면 네이버 종목 검색으로 조회한 뒤 캐시에 저장한다."""
    cache: dict[str, str] = {}
    if ticker_map_path.exists():
        cache = json.loads(ticker_map_path.read_text(encoding="utf-8"))

    if name in cache:
        return cache[name]

    resp = requests.get(
        "https://ac.stock.naver.com/ac",
        params={"q": name, "target": "stock,index,marketindicator"},
        headers={"User-Agent": _UA},
        timeout=10,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    stock_items = [it for it in items if it.get("category") == "stock"]

    match = next((it for it in stock_items if it.get("name") == name), None)
    if match is None and stock_items:
        match = stock_items[0]

    if match is None:
        raise ValueError(
            f"'{name}' 종목을 네이버 검색에서 찾지 못했습니다. "
            f"kr_ticker_map.json에 직접 '{name}': \"종목코드\" 형태로 추가해주세요."
        )

    code = match["code"]
    cache[name] = code
    ticker_map_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return code


def fetch_kr_quote(code: str) -> Quote:
    resp = requests.get(
        f"https://m.stock.naver.com/api/stock/{code}/basic",
        headers={"User-Agent": _UA},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    price = float(data["closePrice"].replace(",", ""))
    change_amount = float(data["compareToPreviousClosePrice"].replace(",", ""))
    direction_code = data["compareToPreviousPrice"]["code"]  # "1"=하락 "2"=상승 "3"=보합
    sign = -1 if direction_code == "1" else (1 if direction_code == "2" else 0)

    pct_change = sign * float(data["fluctuationsRatio"])
    prev_close = price - (sign * change_amount)
    return Quote(price=price, prev_close=prev_close, pct_change=pct_change)


def fetch_kr_opening_price(code: str, on_date: str | None = None) -> float:
    """지정한 날짜(YYYY-MM-DD, 기본값 오늘)의 국내 종목 시가를 조회한다.
    공모주 상장일 시초가 자동 조회용 — basic 엔드포인트엔 시가가 없어서 따로 쓴다."""
    target = on_date or dt.date.today().isoformat()
    resp = requests.get(
        f"https://m.stock.naver.com/api/stock/{code}/price",
        params={"pageSize": 5, "page": 1},
        headers={"User-Agent": _UA},
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()

    for row in rows:
        if row.get("localTradedAt") == target:
            return float(row["openPrice"].replace(",", ""))

    if rows:
        return float(rows[0]["openPrice"].replace(",", ""))

    raise ValueError(f"{code} 종목의 시가 데이터를 찾지 못했습니다.")


def fetch_us_quote(ticker: str) -> Quote:
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        headers={"User-Agent": _UA},
        timeout=10,
    )
    resp.raise_for_status()
    meta = resp.json()["chart"]["result"][0]["meta"]

    price = float(meta["regularMarketPrice"])
    prev_close = float(meta["previousClose"])
    pct_change = (price / prev_close - 1) * 100 if prev_close else 0.0
    return Quote(price=price, prev_close=prev_close, pct_change=pct_change)

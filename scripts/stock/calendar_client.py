"""구글 캘린더 연동 공통 유틸 (이유식 기록 + 주식/공모주 기록용).

이유식 기록은 별도 텔레그램 봇을 새로 만들지 않고 주식 기록 봇(process_trades.py)의
같은 폴링 루프 안에서 처리한다 — getUpdates offset 소비는 파괴적이라 한 봇에 폴러가
둘 있으면 서로의 메시지를 가로채 유실시키기 때문이다 (이 파일 상단이 아니라
process_trades.py의 모듈 docstring에 이 제약이 설명되어 있다).

기존 저장소는 SDK 래퍼 없이 raw requests를 쓰는 스타일이라(telegram_client.py 참고),
캘린더도 google-api-python-client를 새로 추가하지 않고 이미 설치돼 있는 google-auth의
액세스 토큰을 받아 Calendar REST API를 직접 호출한다.

인증은 sheets_client.py와 같은 서비스 계정 키를 재사용하되, 스코프만 캘린더용으로
별도 구성한다. 이 모듈은 서로 다른 두 캘린더를 다룬다 — 어느 함수든 `calendar_id`를
인자로 받으므로 호출부(process_trades.py)가 이유식 캘린더(FOOD_GOOGLE_CALENDAR_ID)와
주식&공모주 캘린더(STOCK_GOOGLE_CALENDAR_ID)를 구분해서 넘긴다. 두 캘린더 모두 이
서비스 계정 이메일과 "일정 변경" 권한으로 공유해둬야 한다 (food_setup_guide.md 참고).

두 가지 이벤트 패턴을 구분해서 제공한다:
  - `upsert_event`: 이유식 기록용 — "하루 1개 이벤트로 누적". 날짜별 결정론적 이벤트
    ID(foodlog + YYYYMMDD)로 구현해서, 검색 없이 GET-by-id로 존재 여부를 알 수 있고
    같은 id로 두 번 생성을 시도하면 API가 409로 막아주므로 경쟁 상태로 같은 날 이벤트가
    두 개 생기는 일도 없다. 이벤트 제목은 그날 먹은 음식 목록(중복 제거, 누적)이고,
    설명(description)에 시간별 상세 로그를 쌓는다.
  - `create_event`: 주식 매매/공모주 청약·상장 기록용 — 거래 건당 독립된 이벤트를 새로
    만든다 (병합하지 않음). id를 지정하지 않고 생성하므로 같은 날 여러 건이어도 서로
    충돌하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import requests
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

API_BASE = "https://www.googleapis.com/calendar/v3"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]

FOOD_LOG_PREFIX = "이유식 기록 - "


def _load_credentials() -> Credentials:
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw_json:
        info = json.loads(raw_json)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if key_path:
        return Credentials.from_service_account_file(key_path, scopes=SCOPES)

    raise RuntimeError(
        "GOOGLE_SERVICE_ACCOUNT_JSON 또는 GOOGLE_APPLICATION_CREDENTIALS 중 "
        "하나가 설정되어 있어야 합니다. food_setup_guide.md를 참고하세요."
    )


def _access_token() -> str:
    creds = _load_credentials()
    creds.refresh(Request())
    return creds.token


def _event_id_for_date(date_str: str) -> str:
    # Calendar API 이벤트 id 허용 문자: 소문자 a-v, 숫자 0-9, 5~1024자.
    return "foodlog" + date_str.replace("-", "")


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen.keys())


def _next_day(date_str: str) -> str:
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=1)).isoformat()


def get_event(calendar_id: str, date_str: str) -> dict | None:
    token = _access_token()
    event_id = _event_id_for_date(date_str)
    resp = requests.get(
        f"{API_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    event = resp.json()
    if event.get("status") == "cancelled":
        return None
    return event


def upsert_event(calendar_id: str, date_str: str, items: list[str], time_str: str) -> None:
    """이유식 기록 — 하루 1개 이벤트에 음식을 누적한다.

    제목은 그날 누적된 음식 목록(중복 제거)이고, 설명에는 시간별 줄이 계속 추가된다.
    """
    token = _access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    event_id = _event_id_for_date(date_str)
    existing = get_event(calendar_id, date_str)
    new_line = f"{time_str} - {', '.join(items)}"

    if existing is None:
        body = {
            "id": event_id,
            "summary": FOOD_LOG_PREFIX + ", ".join(items),
            "description": new_line,
            "start": {"date": date_str},
            "end": {"date": _next_day(date_str)},
        }
        resp = requests.post(
            f"{API_BASE}/calendars/{calendar_id}/events",
            headers=headers,
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        return

    existing_summary = existing.get("summary") or ""
    existing_foods = (
        existing_summary[len(FOOD_LOG_PREFIX):].split(", ")
        if existing_summary.startswith(FOOD_LOG_PREFIX)
        else []
    )
    merged_foods = _dedupe_keep_order(existing_foods + items)
    new_summary = FOOD_LOG_PREFIX + ", ".join(merged_foods)

    existing_description = (existing.get("description") or "").rstrip()
    new_description = f"{existing_description}\n{new_line}" if existing_description else new_line

    resp = requests.patch(
        f"{API_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers=headers,
        json={"summary": new_summary, "description": new_description},
        timeout=10,
    )
    resp.raise_for_status()


def create_event(calendar_id: str, date_str: str, summary: str, description: str = "") -> None:
    """주식 매매/공모주 청약·상장 기록 — 거래 건당 새 종일 이벤트를 하나 만든다.

    id를 지정하지 않아 매번 새 이벤트가 생기므로, 같은 날 여러 건을 기록해도
    서로 병합되지 않고 각각 독립된 이벤트로 남는다.
    """
    token = _access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "summary": summary,
        "description": description,
        "start": {"date": date_str},
        "end": {"date": _next_day(date_str)},
    }
    resp = requests.post(
        f"{API_BASE}/calendars/{calendar_id}/events",
        headers=headers,
        json=body,
        timeout=10,
    )
    resp.raise_for_status()

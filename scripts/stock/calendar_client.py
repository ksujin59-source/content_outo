"""구글 캘린더 연동 공통 유틸 (주식/공모주 기록용).

기존 저장소는 SDK 래퍼 없이 raw requests를 쓰는 스타일이라(telegram_client.py 참고),
캘린더도 google-api-python-client를 새로 추가하지 않고 이미 설치돼 있는 google-auth의
액세스 토큰을 받아 Calendar REST API를 직접 호출한다.

인증은 sheets_client.py와 같은 서비스 계정 키를 재사용하되, 스코프만 캘린더용으로
별도 구성한다. 호출부(process_trades.py, ipo_new_alert.py)는 주식&공모주 기록 전용
캘린더(STOCK_GOOGLE_CALENDAR_ID)의 id를 `calendar_id` 인자로 넘긴다. 이 캘린더는 이
서비스 계정 이메일과 "일정 변경" 권한으로 공유해둬야 한다 (stock_setup_guide.md 참고).

두 가지 이벤트 패턴을 구분해서 제공한다:
  - `create_event`: 주식 매매/공모주 청약·상장 기록용 — 거래 건당 독립된 이벤트를 새로
    만든다 (병합하지 않음). id를 지정하지 않고 생성하므로 같은 날 여러 건이어도 서로
    충돌하지 않는다.
  - `upsert_simple_event`: 공모주 청약/상장 "일정" 동기화용 — 내가 한 행동이 아니라
    38.co.kr에서 긁어온, 이미 확정된 시장 일정을 캘린더에 반영한다. 호출부(ipo_new_alert.py)가
    공모주 번호(no)로 결정론적 id를 만들어 넘기므로, 스크래핑할 때마다 다시 불러도
    같은 이벤트를 덮어쓸 뿐 중복 생성되지 않는다. 내가 실제로 청약한 종목의 "상장일
    보유 현황"(ipo_holding_event_id로 만든 id)도 같은 함수로 관리한다 — 상장예정일이
    확인되는 즉시(아직 매도 전이라 청약가로) 미리 캘린더에 뜨고, 실제로 매도하면
    process_trades.py가 같은 id로 매도가/수익으로 덮어써서 이벤트가 하나로 이어진다.
  - `list_events_for_date`: 아침 캘린더 요약 알림(calendar_daily_digest.py)용 — 그날
    날짜로 걸린 이벤트 제목을 전부 가져온다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

import requests
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

API_BASE = "https://www.googleapis.com/calendar/v3"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]

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
        "하나가 설정되어 있어야 합니다. stock_setup_guide.md를 참고하세요."
    )


def _access_token() -> str:
    creds = _load_credentials()
    creds.refresh(Request())
    return creds.token


def _next_day(date_str: str) -> str:
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=1)).isoformat()


def _prev_day(date_str: str) -> str:
    return (dt.date.fromisoformat(date_str) - dt.timedelta(days=1)).isoformat()


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


def upsert_simple_event(
    calendar_id: str, event_id: str, summary: str, start_date: str, end_date: str,
    description: str | None = None,
) -> None:
    """공모주 청약/상장 "일정"(내가 한 행동이 아니라 시장에 이미 확정된 일정, 또는 상장일
    보유 현황처럼 시간에 따라 내용이 갱신되는 이벤트) 동기화용.

    event_id로 존재 여부를 판단해 있으면 최신 정보로 덮어쓰고(PATCH), 없으면 그
    id로 새로 만든다(POST) — 같은 id로 매번 다시 동기화해도 중복 생성되지 않는다.
    end_date는 종료일 "다음날"(Calendar API의 종일 이벤트 end는 배타적 상한).
    description을 안 주면(None) 기존 설명은 그대로 두고 summary/날짜만 갱신한다.
    """
    token = _access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"summary": summary, "start": {"date": start_date}, "end": {"date": end_date}}
    if description is not None:
        body["description"] = description

    resp = requests.patch(
        f"{API_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers=headers,
        json=body,
        timeout=10,
    )
    if resp.status_code == 404:
        resp = requests.post(
            f"{API_BASE}/calendars/{calendar_id}/events",
            headers=headers,
            json={**body, "id": event_id},
            timeout=10,
        )
    resp.raise_for_status()


def list_events_for_date(calendar_id: str, date_str: str) -> list[str]:
    """해당 날짜(종일 이벤트 기준)에 걸린 이벤트 제목 목록 — 아침 캘린더 요약 알림용.

    종일(date-only) 이벤트의 "하루"는 캘린더의 타임존(KST) 기준인데, timeMin/timeMax는
    타임존 오프셋이 있는 RFC3339라 "Z"(UTC)로 주면 하루 경계가 KST 기준과 9시간
    어긋난다 — 그러면 예를 들어 다음날(KST) 이벤트가 전날 UTC 오후 구간과 겹쳐서
    오늘 조회에도 잘못 섞여 나온다. 그래서 하루 전~다음날까지 넉넉히 조회한 뒤,
    각 이벤트의 start.date 문자열이 정확히 date_str인 것만 클라이언트에서 걸러낸다
    (문자열 그대로 비교하므로 타임존 어긋남 문제 자체가 없다).
    """
    token = _access_token()
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "timeMin": f"{_prev_day(date_str)}T00:00:00Z",
        "timeMax": f"{_next_day(_next_day(date_str))}T00:00:00Z",
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    resp = requests.get(
        f"{API_BASE}/calendars/{calendar_id}/events",
        headers=headers,
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [
        item.get("summary", "(제목 없음)") for item in items
        if item.get("start", {}).get("date") == date_str
    ]


def ipo_holding_event_id(name: str, applicant: str, broker: str) -> str:
    """공모주 청약 1건(종목명+신청인+증권사로 식별)의 "상장일 보유 현황" 이벤트 id.

    상장예정일이 바뀌어도(최초엔 미정이다가 나중에 확정되는 경우) 같은 id를 계속
    쓰므로, 날짜/내용이 바뀌면 upsert_simple_event가 기존 이벤트를 그 자리에서
    갱신한다(날짜가 바뀌면 이벤트가 캘린더에서 그 자리로 옮겨짐).
    """
    digest = hashlib.sha1(f"{name}|{applicant}|{broker}".encode()).hexdigest()
    return "ipohold" + digest[:16]


def ipo_refund_event_id(name: str, applicant: str, broker: str) -> str:
    """같은 청약 1건의 "환불일" 이벤트 id — ipo_holding_event_id와 같은 식별 기준
    (종목명+신청인+증권사)이지만 접두어(salt)를 달리해서 서로 다른 id가 나오게 한다."""
    digest = hashlib.sha1(f"refund|{name}|{applicant}|{broker}".encode()).hexdigest()
    return "iporefund" + digest[:16]

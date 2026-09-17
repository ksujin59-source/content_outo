"""구글시트 연결 공통 유틸.

인증 방식은 둘 중 하나를 지원한다 (순서대로 확인):
  1. GOOGLE_SERVICE_ACCOUNT_JSON 환경변수 — 서비스 계정 키 JSON 전체를 문자열로 담음
     (GitHub Actions Secret에 이 방식으로 등록).
  2. GOOGLE_APPLICATION_CREDENTIALS 환경변수 — 키 JSON 파일 경로 (로컬 테스트용).

시트 안의 탭(워크시트)이 없으면 자동으로 만들고 헤더를 채워준다.
"""

from __future__ import annotations

import json
import os

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEET_SCHEMAS: dict[str, list[str]] = {
    "거래내역": ["날짜", "시장", "종목명", "증권사", "구분", "수량", "가격", "메모"],
    "보유종목": ["시장", "종목명", "종목코드", "증권사", "보유수량", "평단가"],
    "알람로그": ["날짜", "종목명", "발송시각"],
    "상태": ["키", "값"],
    "공모주캘린더": [
        "no", "종목명", "청약시작일", "청약종료일", "확정공모가",
        "희망공모가범위", "주간사", "상장예정일", "환불일",
    ],
    "공모주신청": [
        "청약일", "종목명", "증권사", "신청인", "청약수량", "청약가",
        "상장예정일", "상태", "매도가", "매도일", "수익률",
    ],
    "공모주알람로그": ["날짜", "종목명", "알람유형", "발송시각"],
    "이유식기록": ["날짜", "시간", "음식", "원본메시지"],
}


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


def get_spreadsheet() -> gspread.Spreadsheet:
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID 환경변수가 설정되어 있지 않습니다.")

    client = gspread.authorize(_load_credentials())
    return client.open_by_key(sheet_id)


def get_or_create_worksheet(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    header = SHEET_SCHEMAS[title]
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=200, cols=len(header))
        ws.append_row(header)
        return ws

    if ws.row_values(1) != header:
        ws.update("A1", [header])
    return ws


def get_state(state_ws: gspread.Worksheet, key: str, default: str = "") -> str:
    rows = state_ws.get_all_values()[1:]
    for row in rows:
        if row and row[0] == key:
            return row[1] if len(row) > 1 else default
    return default


def set_state(state_ws: gspread.Worksheet, key: str, value: str) -> None:
    rows = state_ws.get_all_values()
    for idx, row in enumerate(rows[1:], start=2):
        if row and row[0] == key:
            state_ws.update(f"A{idx}:B{idx}", [[key, value]])
            return
    state_ws.append_row([key, value])

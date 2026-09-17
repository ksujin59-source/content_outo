"""텔레그램 봇 API 공통 유틸 (수신용 getUpdates + 발신용 sendMessage).

주식 기록용 전용 봇(STOCK_TELEGRAM_BOT_TOKEN, STOCK_TELEGRAM_CHAT_ID)을 쓴다 — 기존
content-auto 트렌드 알림 봇(TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)과는 별개다. 이 모듈은
GitHub Actions 러너에서 실행되는 것을 전제로 한다 (Claude 클라우드 샌드박스에서는
api.telegram.org 호출이 막혀 있음).
"""

from __future__ import annotations

import os

import requests

API_BASE = "https://api.telegram.org"


def _token() -> str:
    token = os.environ.get("STOCK_TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("STOCK_TELEGRAM_BOT_TOKEN 환경변수가 설정되어 있지 않습니다.")
    return token


def _chat_id() -> str:
    chat_id = os.environ.get("STOCK_TELEGRAM_CHAT_ID")
    if not chat_id:
        raise RuntimeError("STOCK_TELEGRAM_CHAT_ID 환경변수가 설정되어 있지 않습니다.")
    return chat_id


def get_updates(offset: int, timeout: int = 10) -> list[dict]:
    resp = requests.get(
        f"{API_BASE}/bot{_token()}/getUpdates",
        params={"offset": offset, "timeout": 0},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates 실패: {data}")
    return data["result"]


def send_message(text: str) -> None:
    resp = requests.post(
        f"{API_BASE}/bot{_token()}/sendMessage",
        data={"chat_id": _chat_id(), "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"sendMessage 실패: {data}")

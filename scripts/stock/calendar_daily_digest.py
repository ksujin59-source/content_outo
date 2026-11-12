"""매일 아침, 주식&공모주 기록 캘린더(STOCK_GOOGLE_CALENDAR_ID)에 오늘 날짜로 걸린
일정을 모아 텔레그램으로 요약해서 보낸다.

예: 공모주 신규/확정/청약/상장 "일정"(ipo_new_alert.py가 upsert_simple_event로 동기화해둔
것)과, 실제로 한 매수/매도/청약/상장 "기록"(process_trades.py가 create_event로 만든 것)이
섞여서 보일 수 있다 — 종류를 가리지 않고 그날 캘린더에 걸린 이벤트를 전부 보여준다.

GitHub Actions cron이 KST 아침 한 시점(원래 07:00 1회)을 겨냥했었는데, 이 저장소의
다른 빈번한 schedule 워크플로우들과 마찬가지로 GitHub가 scheduled trigger를 상당히
지연시켜서(실제로 3시간 가까이 늦어져 10시경에야 발송된 적이 있었음) 하루에 한 번뿐인
실행이 아예 늦은 시각에 몰리는 문제가 있었다. 그래서 08:00 KST까지는 반드시 도착하도록
06:00~08:00 사이 여러 cron 슬롯으로 재시도 기회를 늘리고, 그중 가장 먼저 성공한 실행만
보내도록 "상태" 탭(키: calendar_digest_last_date)에 오늘 날짜를 기록해 같은 날 중복
발송을 막는다.

로컬 실행 시엔 같은 폴더의 .env를 자동으로 읽는다.
"""

from __future__ import annotations

import datetime as dt
import os
import zoneinfo
from pathlib import Path

from dotenv import load_dotenv

import calendar_client
import sheets_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

KST = zoneinfo.ZoneInfo("Asia/Seoul")

STATE_KEY = "calendar_digest_last_date"


def main() -> None:
    calendar_id = os.environ["STOCK_GOOGLE_CALENDAR_ID"]
    today = dt.datetime.now(KST).date().isoformat()

    spreadsheet = sheets_client.get_spreadsheet()
    ws_state = sheets_client.get_or_create_worksheet(spreadsheet, "상태")

    if sheets_client.get_state(ws_state, STATE_KEY) == today:
        print(f"오늘({today}) 이미 발송함.")
        return

    titles = calendar_client.list_events_for_date(calendar_id, today)

    if titles:
        lines = "\n".join(f"- {t}" for t in titles)
        text = f"📅 오늘({today}) 일정\n{lines}"
    else:
        text = f"📅 오늘({today}) 캘린더 일정 없음"

    print(text)
    telegram_client.send_message(text)
    sheets_client.set_state(ws_state, STATE_KEY, today)


if __name__ == "__main__":
    main()

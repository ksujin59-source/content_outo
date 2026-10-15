"""매일 아침, 주식&공모주 기록 캘린더(STOCK_GOOGLE_CALENDAR_ID)에 오늘 날짜로 걸린
일정을 모아 텔레그램으로 요약해서 보낸다.

예: 공모주 신규/확정/청약/상장 "일정"(ipo_new_alert.py가 upsert_simple_event로 동기화해둔
것)과, 실제로 한 매수/매도/청약/상장 "기록"(process_trades.py가 create_event로 만든 것)이
섞여서 보일 수 있다 — 종류를 가리지 않고 그날 캘린더에 걸린 이벤트를 전부 보여준다.

GitHub Actions cron으로 매일 KST 07:00 1회 실행되는 것을 전제로 한다(다른 알림들과
달리 하루에 한 번만 돌므로 알람로그 중복 방지는 두지 않는다 — 수동으로 같은 날 여러
번 실행하면 그만큼 중복 발송될 수 있다).

로컬 실행 시엔 같은 폴더의 .env를 자동으로 읽는다.
"""

from __future__ import annotations

import datetime as dt
import os
import zoneinfo
from pathlib import Path

from dotenv import load_dotenv

import calendar_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

KST = zoneinfo.ZoneInfo("Asia/Seoul")


def main() -> None:
    calendar_id = os.environ["STOCK_GOOGLE_CALENDAR_ID"]
    today = dt.datetime.now(KST).date().isoformat()

    titles = calendar_client.list_events_for_date(calendar_id, today)

    if titles:
        lines = "\n".join(f"- {t}" for t in titles)
        text = f"📅 오늘({today}) 일정\n{lines}"
    else:
        text = f"📅 오늘({today}) 캘린더 일정 없음"

    print(text)
    telegram_client.send_message(text)


if __name__ == "__main__":
    main()

"""공모주 상장일 매도 리마인더 — 상장 전날 저녁 20:00~20:30, 그리고 상장 당일 08:20~08:40에 알린다.

GitHub Actions cron이 평일 30분 간격 넓은 구간으로 이 스크립트를 호출하는 것을 전제로
한다 (check_prices.py와 같은 패턴). 정확한 08:30 판단과 "며칠에 한 번만" 판단은 이
스크립트 내부에서 zoneinfo로 하고, 공모주알람로그 탭으로 같은 날 중복 발송을 막는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import zoneinfo
from pathlib import Path

from dotenv import load_dotenv

import sheets_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

KST = zoneinfo.ZoneInfo("Asia/Seoul")


def _already_alerted(ws_alarm_log, today: str, name: str, alert_type: str) -> bool:
    for row in ws_alarm_log.get_all_values()[1:]:
        if len(row) >= 3 and row[0] == today and row[1] == name and row[2] == alert_type:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force-alert",
        metavar="종목명:알람유형",
        help="시간대/중복방지 조건을 무시하고 즉시 테스트 알람 발송 (예: 바로팜:당일)",
    )
    args = parser.parse_args()

    spreadsheet = sheets_client.get_spreadsheet()
    ws_apply = sheets_client.get_or_create_worksheet(spreadsheet, "공모주신청")
    ws_alarm_log = sheets_client.get_or_create_worksheet(spreadsheet, "공모주알람로그")

    now_kst = dt.datetime.now(KST)
    today = now_kst.date().isoformat()
    tomorrow = (now_kst.date() + dt.timedelta(days=1)).isoformat()
    in_morning_window = dt.time(8, 20) <= now_kst.time() <= dt.time(8, 40)
    in_evening_window = dt.time(20, 0) <= now_kst.time() <= dt.time(20, 30)

    force_name, force_type = (None, None)
    if args.force_alert:
        force_name, force_type = args.force_alert.split(":", 1)

    pending = [
        row for row in ws_apply.get_all_values()[1:]
        if len(row) >= 8 and row[7] == "대기"
    ]
    sent = 0

    for row in pending:
        name, broker, applicant, listing_date = row[1], row[2], row[3], row[6]
        if not listing_date:
            continue

        checks = []
        if force_name == name:
            checks.append(force_type)
        else:
            if listing_date == tomorrow and in_evening_window:
                checks.append("전날")
            if listing_date == today and in_morning_window:
                checks.append("당일")

        for alert_type in checks:
            if not args.force_alert and _already_alerted(ws_alarm_log, today, name, alert_type):
                continue

            if alert_type == "전날":
                text = f"⏰ 내일 {name} 상장! 매도 잊지 마세요\n{broker} · {applicant}"
            else:
                text = f"🔔 오늘 {name} 상장일! 지금 매도 확인하세요\n{broker} · {applicant}"

            telegram_client.send_message(text)
            if not args.force_alert:
                ws_alarm_log.append_row([today, name, alert_type, now_kst.strftime("%H:%M")])
            sent += 1

    print(f"알람 {sent}건 발송, 대기중 {len(pending)}건 확인.")


if __name__ == "__main__":
    main()

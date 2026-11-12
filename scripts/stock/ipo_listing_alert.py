"""공모주 상장일 매도 리마인더 — 상장 전날, 그리고 상장 당일에 알린다.

GitHub Actions cron에 "*/10"으로 10분 간격을 줘봤지만, 실제로는 GitHub가 빈번한
schedule 트리거를 상당히 잡아먹어서(공식 문서가 말하는 정도를 훨씬 넘어서) 하루에
실제로 도는 횟수가 3번 안팎에 불과하고, 그마저 지정한 cron 슬롯 시각과 거리가 먼
임의 시각에 실행되는 경우가 흔하다 — 그래서 원래 08:30~09:50처럼 좁은 창을 뒀을 때,
그날 실제로 돈 유일한 실행이 그 창을 그냥 벗어나버려서 "당일" 알림이 통째로 안 나간
사고가 실제로 있었다(2026-10-20, 엘리스그룹). 좁은 시간창으로 "그날 아침에"를
흉내내는 건 이 실행 빈도로는 신뢰할 수 없다고 판단해서, 창을 거의 하루 전체(당일은
08:00~21:00, 전날은 12:00~23:59)로 넓혔다 — 어차피 "며칠에 한 번만" 판단은
공모주알람로그 탭으로 같은 날 중복 발송을 막으니, 창을 넓혀도 같은 알림이 하루에
여러 번 가지는 않는다.

매도 리마인더에는 증권사와 함께 내가 청약한 공모가(청약가)도 같이 보여준다 — 실시간
시초가 조회는 안 하고(실패/지연 위험, 사용자가 직접 비교 판단하길 선호) 이미 시트에
있는 값을 그대로 쓴다.

"배정받은 건 상장 당일에 꼭 팔아야 한다"는 핵심 지시는 "전날" 알림을 1차 채널로 삼는다
— "당일" 알림도 남겨두지만(위에서 설명한 이유로 실행 자체가 늦게 돌 수 있어서), 그날
장이 다 끝나갈 때쯔음에야 늦게 도착하면 실질적으로 매도 타이밍을 놓친 뒤라 의미가
없어진다. 반면 "전날"은 하루 전체(12:00~23:59)가 창이라 실행이 늦어져도 다음날 아침
장 시작 전까지 여유가 있다 — 그래서 "전날" 쪽 문구를 "꼭 매도하세요"로 단정적으로
바꾸고, "당일"은 혹시 전날 알림을 놓쳤을 때를 위한 보조 확인용으로 남겨둔다.
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
    in_morning_window = dt.time(8, 0) <= now_kst.time() <= dt.time(21, 0)
    in_evening_window = dt.time(12, 0) <= now_kst.time() <= dt.time(23, 59)

    force_name, force_type = (None, None)
    if args.force_alert:
        force_name, force_type = args.force_alert.split(":", 1)

    pending = [
        row for row in ws_apply.get_all_values()[1:]
        if len(row) >= 8 and row[7] == "대기"
    ]
    sent = 0

    for row in pending:
        name, broker, applicant, price, listing_date = row[1], row[2], row[3], row[5], row[6]
        if not listing_date:
            continue

        try:
            price_label = f"{float(price):,.0f}원"
        except (ValueError, TypeError):
            price_label = price or "미정"

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
                text = (
                    f"⚠️ 내일 {name} 상장일입니다! 상장 당일 꼭 매도하세요\n"
                    f"증권사 {broker or '미정'} · {applicant} · 청약가 {price_label}"
                )
            else:
                text = (
                    f"🔔 오늘 {name} 상장일! (전날 알림 못 보셨다면) 지금 바로 매도 확인하세요\n"
                    f"증권사 {broker or '미정'} · {applicant} · 청약가 {price_label}"
                )

            telegram_client.send_message(text)
            if not args.force_alert:
                ws_alarm_log.append_row([today, name, alert_type, now_kst.strftime("%H:%M")])
            sent += 1

    print(f"알람 {sent}건 발송, 대기중 {len(pending)}건 확인.")


if __name__ == "__main__":
    main()

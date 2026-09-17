"""보유 종목 시세를 조회해서 전일 종가 대비 급변동(기본 ±7%) 시 텔레그램 알람을 보낸다.

GitHub Actions cron으로 장중에 30분 간격 실행되는 것을 전제로 한다. cron 자체는 느슨한
UTC 구간으로 걸어두고, 실제 개장 여부는 이 스크립트가 종목별 시장 기준 시간으로 판단한다.
같은 날 같은 종목에 대해서는 한 번만 알람을 보낸다 (알람로그 탭으로 중복 방지).

로컬 실행 시엔 같은 폴더의 .env를 자동으로 읽는다 (없으면 무시하고 이미 설정된
환경변수를 그대로 씀).
"""

from __future__ import annotations

import argparse
import datetime as dt
import zoneinfo
from pathlib import Path

from dotenv import load_dotenv

import price_lookup
import sheets_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

THRESHOLD_PCT = 7.0
KST = zoneinfo.ZoneInfo("Asia/Seoul")
ET = zoneinfo.ZoneInfo("America/New_York")


def _kr_market_open(now_kst: dt.datetime) -> bool:
    if now_kst.weekday() >= 5:
        return False
    return dt.time(9, 0) <= now_kst.time() <= dt.time(15, 30)


def _us_market_open(now_et: dt.datetime) -> bool:
    if now_et.weekday() >= 5:
        return False
    return dt.time(9, 30) <= now_et.time() <= dt.time(16, 0)


def _load_holdings(ws) -> list[dict]:
    holdings = []
    for row in ws.get_all_values()[1:]:
        if len(row) < 5:
            continue
        market, name, code, broker = row[0], row[1], row[2], row[3]
        qty = int(row[4]) if row[4] else 0
        if qty <= 0 or not code:
            continue
        holdings.append({"market": market, "name": name, "code": code, "broker": broker, "qty": qty})
    return holdings


def _already_alerted(ws_alarm_log, today: str, name: str) -> bool:
    for row in ws_alarm_log.get_all_values()[1:]:
        if len(row) >= 2 and row[0] == today and row[1] == name:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force-alert",
        metavar="종목명",
        help="장중/임계치 조건을 무시하고 해당 종목에 대해 테스트 알람을 즉시 발송",
    )
    args = parser.parse_args()

    spreadsheet = sheets_client.get_spreadsheet()
    ws_holdings = sheets_client.get_or_create_worksheet(spreadsheet, "보유종목")
    ws_alarm_log = sheets_client.get_or_create_worksheet(spreadsheet, "알람로그")

    holdings = _load_holdings(ws_holdings)
    now_kst = dt.datetime.now(KST)
    now_et = now_kst.astimezone(ET)
    today = now_kst.date().isoformat()

    if args.force_alert:
        holdings = [h for h in holdings if h["name"] == args.force_alert] or [
            {"market": "국내", "name": args.force_alert, "code": args.force_alert, "broker": "", "qty": 0}
        ]

    for holding in holdings:
        market, name, code, broker = holding["market"], holding["name"], holding["code"], holding["broker"]

        if not args.force_alert:
            is_open = _kr_market_open(now_kst) if market == "국내" else _us_market_open(now_et)
            if not is_open:
                continue

        quote = price_lookup.fetch_kr_quote(code) if market == "국내" else price_lookup.fetch_us_quote(code)
        print(f"{market} {name}({code}, {broker}): {quote.price:,.2f} ({quote.pct_change:+.2f}%)")

        breached = abs(quote.pct_change) >= THRESHOLD_PCT
        if not (breached or args.force_alert):
            continue
        if not args.force_alert and _already_alerted(ws_alarm_log, today, name):
            continue

        now_str = now_kst.strftime("%H:%M")
        direction = "급등" if quote.pct_change > 0 else "급락"
        text = (
            f"🚨 {name} {direction} 알림\n"
            f"현재가 {quote.price:,.2f} (전일 대비 {quote.pct_change:+.2f}%)"
        )
        telegram_client.send_message(text)
        if not args.force_alert:
            ws_alarm_log.append_row([today, name, now_str])


if __name__ == "__main__":
    main()

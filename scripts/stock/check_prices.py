"""보유 종목 시세를 조회해서 전일 종가 대비 급변동(기본 ±7%) 시 텔레그램 알람을 보낸다.
코스피 지수가 전일 대비 ±5% 이상(급등/급락 둘 다) 변동한 날에도 같은 방식으로 알린다.

나스닥/S&P500은 성격이 달라서 따로 처리한다 — 급변동 임계치가 아니라 "밤사이 미국장이
오르고 내렸는지"를 매일 아침 1회, 등락폭과 상관없이 무조건 알려주는 요약 알림이다
(07:00~08:50 KST, 화~토 아침에 각각 월~금 마감분을 챙긴다). 월요일/일요일 아침은 제외한다
— 주말엔 거래가 없어서 토요일에 이미 보낸 금요일 종가와 같은 값이 월요일에 또 나가는
중복이 생기기 때문.

같은 날 같은 종목(코스피는 "코스피", 나스닥/S&P500 요약은 "나스닥"/"S&P500"을 종목명
자리에 씀)에 대해서는 한 번만 알람을 보낸다 (알람로그 탭으로 중복 방지).

GitHub Actions cron으로 장중에 30분 간격 실행되는 것을 전제로 한다. cron 자체는 느슨한
UTC 구간으로 걸어두고, 실제 개장 여부/알림 시점은 이 스크립트가 판단한다.

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
KOSPI_THRESHOLD_PCT = 5.0
NASDAQ_TICKER = "^IXIC"
SP500_TICKER = "^GSPC"
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


def _nasdaq_summary_window(now_kst: dt.datetime) -> bool:
    """나스닥/S&P500 아침 요약 발송 시간대 — 화~토(미국 월~금 마감분을 그 다음날 아침에
    챙김) 07:00~08:50 KST. 월요일은 주말 내내 거래가 없어 토요일에 보낸 금요일 종가와
    같은 값이 또 나가는 중복이라 제외하고, 일요일도 같은 이유(週 마감 없음)로 제외한다."""
    if now_kst.weekday() in (0, 6):  # 월요일, 일요일 제외 (금요일 종가 중복 방지)
        return False
    return dt.time(7, 0) <= now_kst.time() <= dt.time(8, 50)


def _send(text: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] 전송 생략:\n{text}")
    else:
        telegram_client.send_message(text)


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
        help='장중/임계치 조건을 무시하고 해당 종목(또는 "코스피", 혹은 "나스닥"/"S&P500" 둘 중 하나를 넣으면 둘 다)에 대해 테스트 알람을 즉시 발송',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 텔레그램 전송 없이 콘솔에만 출력 (테스트용, 알람로그에도 기록하지 않음)",
    )
    args = parser.parse_args()

    spreadsheet = sheets_client.get_spreadsheet()
    ws_holdings = sheets_client.get_or_create_worksheet(spreadsheet, "보유종목")
    ws_alarm_log = sheets_client.get_or_create_worksheet(spreadsheet, "알람로그")

    now_kst = dt.datetime.now(KST)
    now_et = now_kst.astimezone(ET)
    today = now_kst.date().isoformat()
    now_str = now_kst.strftime("%H:%M")

    force_kospi = args.force_alert == "코스피"
    force_nasdaq = args.force_alert in ("나스닥", "S&P500")

    if not force_kospi and not force_nasdaq:
        holdings = _load_holdings(ws_holdings)
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

            direction = "급등" if quote.pct_change > 0 else "급락"
            text = (
                f"🚨 {name} {direction} 알림\n"
                f"현재가 {quote.price:,.2f} (전일 대비 {quote.pct_change:+.2f}%)"
            )
            _send(text, args.dry_run)
            if not args.force_alert and not args.dry_run:
                ws_alarm_log.append_row([today, name, now_str])

    if force_kospi or _kr_market_open(now_kst):
        kospi = price_lookup.fetch_kr_index_quote("KOSPI")
        print(f"코스피: {kospi.price:,.2f} ({kospi.pct_change:+.2f}%)")

        breached = abs(kospi.pct_change) >= KOSPI_THRESHOLD_PCT
        if breached or force_kospi:
            if force_kospi or not _already_alerted(ws_alarm_log, today, "코스피"):
                direction = "급등" if kospi.pct_change > 0 else "급락"
                text = (
                    f"🚨 코스피 {direction} 알림\n"
                    f"현재 {kospi.price:,.2f} (전일 대비 {kospi.pct_change:+.2f}%)"
                )
                _send(text, args.dry_run)
                if not force_kospi and not args.dry_run:
                    ws_alarm_log.append_row([today, "코스피", now_str])

    for label, ticker, force_flag in (
        ("나스닥", NASDAQ_TICKER, force_nasdaq),
        ("S&P500", SP500_TICKER, force_nasdaq),
    ):
        if force_flag or _nasdaq_summary_window(now_kst):
            if force_flag or not _already_alerted(ws_alarm_log, today, label):
                quote = price_lookup.fetch_us_quote(ticker)
                print(f"{label}: {quote.price:,.2f} ({quote.pct_change:+.2f}%)")

                direction = "상승" if quote.pct_change > 0 else ("하락" if quote.pct_change < 0 else "보합")
                text = (
                    f"🌙 밤사이 {label} {direction}\n"
                    f"종가 {quote.price:,.2f} (전일 대비 {quote.pct_change:+.2f}%)"
                )
                _send(text, args.dry_run)
                if not force_flag and not args.dry_run:
                    ws_alarm_log.append_row([today, label, now_str])


if __name__ == "__main__":
    main()

"""매주 금요일, 앞으로(오늘 이후) 상장 예정인 공모주의 일정이 38.co.kr 기준과
여전히 일치하는지 재확인해서 바로잡는다.

ipo_new_alert.py의 보강 루프는 상장예정일/환불일이 "비어있을 때만" 상세 페이지를
재조회한다 — 한 번 채워지면 다시는 확인하지 않는다. 그런데 공모주 상장일은 확정
이후에도 연기되거나 당겨지는 경우가 실제로 있어서, 이미 기록해둔 날짜가 나중에
바뀌어도 시트/캘린더가 계속 옛 날짜를 들고 있게 되는 문제가 있다. 이 스크립트는
그 간극을 메우기 위해, 아직 지나지 않은(오늘 이후) 상장예정일을 가진 모든 행을
매주 한 번 무조건 재조회해서 날짜가 바뀌었으면 공모주캘린더/공모주신청 탭과 구글
캘린더 이벤트를 전부 갱신하고, 바뀐 건에 대해서만 텔레그램으로 알린다.
"""

from __future__ import annotations

import datetime as dt
import os
import zoneinfo
from pathlib import Path

from dotenv import load_dotenv

import calendar_client
import ipo_calendar
import sheets_client
import telegram_client
from ipo_new_alert import _next_iso, _to_iso

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

KST = zoneinfo.ZoneInfo("Asia/Seoul")


def _force_sync_holding(
    ws_apply, stock_calendar_id: str, name: str, listing_date_iso: str, refund_date_iso: str,
) -> None:
    """ipo_new_alert.py의 _sync_holding_events와 같은 매칭 로직이지만, 상장예정일이
    이미 채워져 있어도(바뀌었다면) 덮어쓴다 — 여기선 "비어있을 때만 채우기"가 아니라
    "바뀌었으면 고치기"가 목적이기 때문."""
    if not stock_calendar_id or not (listing_date_iso or refund_date_iso):
        return
    rows = ws_apply.get_all_values()[1:]
    for idx, row in enumerate(rows, start=2):
        if len(row) < 8 or row[7] != "대기":
            continue
        row_name = row[1]
        if not (row_name == name or name in row_name or row_name in name):
            continue
        broker, applicant = row[2], row[3]
        try:
            qty, price = int(row[4]), float(row[5])
        except ValueError:
            continue

        if listing_date_iso and (len(row) <= 6 or row[6] != listing_date_iso):
            ws_apply.update(f"G{idx}", [[listing_date_iso]])

        if listing_date_iso:
            calendar_client.upsert_simple_event(
                stock_calendar_id, calendar_client.ipo_holding_event_id(row_name, applicant, broker),
                f"[상장] {row_name} {qty}주 {price:,.0f}원 {broker}",
                listing_date_iso, _next_iso(listing_date_iso),
            )
        if refund_date_iso:
            calendar_client.upsert_simple_event(
                stock_calendar_id, calendar_client.ipo_refund_event_id(row_name, applicant, broker),
                f"[환불일] {row_name} {qty}주 {price:,.0f}원 {broker}",
                refund_date_iso, _next_iso(refund_date_iso),
            )


def main() -> None:
    stock_calendar_id = os.environ.get("STOCK_GOOGLE_CALENDAR_ID", "")

    spreadsheet = sheets_client.get_spreadsheet()
    ws_calendar = sheets_client.get_or_create_worksheet(spreadsheet, "공모주캘린더")
    ws_apply = sheets_client.get_or_create_worksheet(spreadsheet, "공모주신청")

    today = dt.datetime.now(KST).date().isoformat()
    rows = ws_calendar.get_all_values()[1:]

    checked = 0
    changed = 0
    for idx, row in enumerate(rows, start=2):
        if not row:
            continue
        no = row[0]
        name = row[1] if len(row) > 1 else ""
        lead_manager = row[6] if len(row) > 6 else ""
        old_listing = row[7] if len(row) > 7 else ""
        old_refund = row[8] if len(row) > 8 else ""

        if old_listing and old_listing < today:
            continue  # 이미 지난 상장일은 재확인 대상 아님 — "오늘 이후"만 본다

        checked += 1
        detail = ipo_calendar.fetch_detail(no)
        new_listing = _to_iso(detail["listing_date"])
        new_refund = _to_iso(detail["refund_date"])

        if new_listing and new_listing != old_listing:
            ws_calendar.update(f"H{idx}", [[new_listing]])
            telegram_client.send_message(
                f"⚠️ {name} 상장예정일 변경: {old_listing or '미정'} → {new_listing} (주간사 {lead_manager})"
            )
            changed += 1
        if new_refund and new_refund != old_refund:
            ws_calendar.update(f"I{idx}", [[new_refund]])

        listing_date_iso = new_listing or old_listing
        refund_date_iso = new_refund or old_refund

        if stock_calendar_id and listing_date_iso:
            calendar_client.upsert_simple_event(
                stock_calendar_id, f"ipolist{no}", f"[상장예정] {name} · {lead_manager}",
                listing_date_iso, _next_iso(listing_date_iso),
            )
        if stock_calendar_id and refund_date_iso:
            calendar_client.upsert_simple_event(
                stock_calendar_id, f"iporefunddate{no}", f"[환불일] {name} · {lead_manager}",
                refund_date_iso, _next_iso(refund_date_iso),
            )
        _force_sync_holding(ws_apply, stock_calendar_id, name, listing_date_iso, refund_date_iso)

    print(f"재확인 {checked}건, 변경 {changed}건.")


if __name__ == "__main__":
    main()

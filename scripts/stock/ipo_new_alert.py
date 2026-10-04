"""38.co.kr 공모주 목록을 스크래핑해서 신규 청약 건을 감지하고, 이후 단계별 알림을 보낸다.

공모주캘린더 탭에 없는 no(38.co.kr 내부 고유번호)가 나오면 신규로 간주해 행을 추가하고
"신규 발견" 알림을 보낸다. 최초 실행 시엔 현재 청약 가능한 공모주 전체가 한꺼번에
"신규"로 잡혀 여러 건 알림이 갈 수 있는데, 이는 의도된 동작이다(지금 뭐가 열려있는지
따라잡기).

이미 추적 중인 행에 대해서는 매 실행마다 아래 네 가지를 확인해서 각각 최대 1회씩 알린다
(공모주알람로그 탭으로 중복 발송을 막는다 — ipo_listing_alert.py와 동일한 dedup 패턴). 네
알림 모두 어느 증권사(주간사) 건인지 메시지에 포함한다:
  - 공모가확정: 확정공모가가 비어있다가 채워지는 순간
  - 일주일전: 오늘이 청약시작일 - 7일
  - 첫날: 오늘이 청약시작일 (cron이 평일 9/13/18시에 도므로 그날 첫 실행인 9시경에 나감)
  - 마지막날: 오늘이 청약종료일이면서 KST 13:50 이후(처음엔 13:50~14:10 사이로만
    한정했었는데, GitHub Actions의 schedule 트리거가 정각 슬롯에서 수 분~수십 분씩
    밀리거나 통째로 스킵되는 일이 실제로 잦아서(2026-10-01엔 14:30, 10-02엔 14:18에야
    실행됨) 좁은 시간창을 매번 놓쳤다 — 하한만 두어, 14시 슬롯이 늦거나 아예 스킵돼도
    그날 이후 돌아가는 실행(예: 18시 슬롯)에서라도 반드시 나가도록 완화함)

상장예정일이 아직 미확정(빈 값)인 기존 행은 매 실행마다 상세 페이지를 재조회해서 보강한다.

신규/기존 모든 행에 대해 매 실행마다 "확인된 공모주 일정"을 STOCK_GOOGLE_CALENDAR_ID
캘린더에도 동기화한다 — 이건 내가 청약/매도한 "행동" 기록(process_trades.py가 담당)이
아니라, 시장에 이미 확정된 "일정"이라 별도 함수(calendar_client.upsert_simple_event)로
공모주 번호(no) 기반 결정론적 id를 써서 반영한다(재실행해도 중복 생성되지 않고 최신
정보로 덮어씀): 청약기간을 "[청약] 종목명 (가격)" 이벤트로, 상장예정일이 확인되면
"[상장예정] 종목명" 이벤트로, 환불일이 확인되면 "[환불일] 종목명" 이벤트로 만든다.

상장예정일이 확인될 때마다, 공모주신청 탭에 그 종목으로 대기중인(아직 매도 안 한) 내
신청 건이 있는지도 찾아서 "[상장] 종목명 수량주 가격원 증권사" 이벤트를 상장예정일에
미리 띄워준다(아직 매도 전이라 청약가 기준) — process_trades.py에서 청약할 때 이미
상장예정일을 알면 바로 띄워주지만, 그때 몰랐다가 나중에(이 스크립트가 재조회해서)
확인되는 경우를 보강하기 위함. 실제로 매도하면 process_trades.py가 같은 id
(calendar_client.ipo_holding_event_id)로 매도가/수익으로 덮어써서 이벤트가 하나로
이어진다.
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

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

KST = zoneinfo.ZoneInfo("Asia/Seoul")


def _to_iso(date_str: str) -> str:
    return date_str.replace(".", "-") if date_str else ""


def _next_iso(date_str: str) -> str:
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=1)).isoformat()


def _price_label(listing: ipo_calendar.IpoListing) -> str:
    price = listing.fixed_price if listing.fixed_price and listing.fixed_price != "-" else listing.price_range
    return f"{price}원"


def _sync_calendar(
    stock_calendar_id: str, listing: ipo_calendar.IpoListing, listing_date_iso: str, refund_date_iso: str,
) -> None:
    if not stock_calendar_id:
        return

    start_iso = _to_iso(listing.subscribe_start)
    end_iso = _to_iso(listing.subscribe_end)
    if start_iso and end_iso:
        calendar_client.upsert_simple_event(
            stock_calendar_id, f"ipoapp{listing.no}",
            f"[청약] {listing.name} ({_price_label(listing)}) · {listing.lead_manager}",
            start_iso, _next_iso(end_iso),
        )

    if listing_date_iso:
        calendar_client.upsert_simple_event(
            stock_calendar_id, f"ipolist{listing.no}",
            f"[상장예정] {listing.name} · {listing.lead_manager}",
            listing_date_iso, _next_iso(listing_date_iso),
        )

    if refund_date_iso:
        calendar_client.upsert_simple_event(
            stock_calendar_id, f"iporefunddate{listing.no}",
            f"[환불일] {listing.name} · {listing.lead_manager}",
            refund_date_iso, _next_iso(refund_date_iso),
        )


def _sync_holding_events(
    ws_apply, stock_calendar_id: str, name: str, listing_date_iso: str, refund_date_iso: str,
) -> None:
    """상장예정일/환불일이 확인된 종목으로 대기중인 내 신청 건이 있으면 "상장일 보유
    현황"/"환불일" 이벤트를 미리 띄운다(아직 매도 전이라 청약가 기준) — 청약 시점엔
    그 날짜들을 몰랐다가 나중에 확인되는 경우를 보강.

    공모주신청 탭의 상장예정일(G열) 자체도 비어있으면 같이 채운다 — ipo_listing_alert.py
    (상장 전날/당일 매도 리마인더)가 이 칸만 보고 판단하는데, 청약 시점에 상장예정일을
    몰랐으면 이 칸이 영원히 빈 채로 남아 "당일 아침 8시" 알림이 영영 안 나가는 문제가
    있었다 — 여기서 채워줘야 그 알림이 걸릴 기회가 생긴다."""
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

        if listing_date_iso and (len(row) <= 6 or not row[6]):
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


def _already_alerted(ws_alarm_log, today: str, name: str, alert_type: str) -> bool:
    for row in ws_alarm_log.get_all_values()[1:]:
        if len(row) >= 3 and row[0] == today and row[1] == name and row[2] == alert_type:
            return True
    return False


def _send_alert(ws_alarm_log, today: str, now_kst: dt.datetime, name: str, alert_type: str, text: str) -> None:
    if _already_alerted(ws_alarm_log, today, name, alert_type):
        return
    telegram_client.send_message(text)
    ws_alarm_log.append_row([today, name, alert_type, now_kst.strftime("%H:%M")])


def main() -> None:
    stock_calendar_id = os.environ.get("STOCK_GOOGLE_CALENDAR_ID", "")

    spreadsheet = sheets_client.get_spreadsheet()
    ws_calendar = sheets_client.get_or_create_worksheet(spreadsheet, "공모주캘린더")
    ws_alarm_log = sheets_client.get_or_create_worksheet(spreadsheet, "공모주알람로그")
    ws_apply = sheets_client.get_or_create_worksheet(spreadsheet, "공모주신청")

    rows = ws_calendar.get_all_values()[1:]
    existing = {row[0]: idx for idx, row in enumerate(rows, start=2) if row}
    existing_listing_date = {row[0]: (row[7] if len(row) > 7 else "") for row in rows if row}
    existing_refund_date = {row[0]: (row[8] if len(row) > 8 else "") for row in rows if row}
    existing_price = {row[0]: (row[4] if len(row) > 4 else "") for row in rows if row}

    now_kst = dt.datetime.now(KST)
    today = now_kst.date().isoformat()
    afternoon_or_later = now_kst.time() >= dt.time(13, 50)

    listings = ipo_calendar.fetch_list()
    new_count = 0

    for listing in listings:
        if listing.no not in existing:
            detail = ipo_calendar.fetch_detail(listing.no)
            listing_date_iso = _to_iso(detail["listing_date"])
            refund_date_iso = _to_iso(detail["refund_date"])
            ws_calendar.append_row([
                listing.no,
                listing.name,
                _to_iso(listing.subscribe_start),
                _to_iso(listing.subscribe_end),
                listing.fixed_price,
                listing.price_range,
                listing.lead_manager,
                listing_date_iso,
                refund_date_iso,
            ])
            telegram_client.send_message(
                f"🆕 새 공모주: {listing.name}\n"
                f"청약 {listing.subscribe_start}~{listing.subscribe_end[5:]}\n"
                f"희망공모가 {listing.price_range}원\n"
                f"주간사 {listing.lead_manager}"
            )
            _sync_calendar(stock_calendar_id, listing, listing_date_iso, refund_date_iso)
            _sync_holding_events(ws_apply, stock_calendar_id, listing.name, listing_date_iso, refund_date_iso)
            new_count += 1
            continue

        row_idx = existing[listing.no]
        listing_date_iso = existing_listing_date.get(listing.no, "")
        refund_date_iso = existing_refund_date.get(listing.no, "")

        if not existing_price.get(listing.no) and listing.fixed_price:
            ws_calendar.update(f"E{row_idx}", [[listing.fixed_price]])
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "공모가확정",
                f"💰 {listing.name} 공모가 확정: {listing.fixed_price}원 (주간사 {listing.lead_manager})",
            )

        if not listing_date_iso:
            detail = ipo_calendar.fetch_detail(listing.no)
            if detail["listing_date"]:
                listing_date_iso = _to_iso(detail["listing_date"])
                refund_date_iso = _to_iso(detail["refund_date"])
                ws_calendar.update(f"H{row_idx}:I{row_idx}", [[
                    listing_date_iso, refund_date_iso,
                ]])

        _sync_calendar(stock_calendar_id, listing, listing_date_iso, refund_date_iso)
        _sync_holding_events(ws_apply, stock_calendar_id, listing.name, listing_date_iso, refund_date_iso)

        start_iso = _to_iso(listing.subscribe_start)
        end_iso = _to_iso(listing.subscribe_end)
        week_before_iso = (
            (dt.date.fromisoformat(start_iso) - dt.timedelta(days=7)).isoformat() if start_iso else ""
        )

        if week_before_iso and today == week_before_iso:
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "일주일전",
                f"📅 {listing.name} 청약 일주일 전! 청약 {listing.subscribe_start}~{listing.subscribe_end[5:]}"
                f" (주간사 {listing.lead_manager})",
            )

        if start_iso and today == start_iso:
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "첫날",
                f"🔔 {listing.name} 청약 첫날입니다! ~{listing.subscribe_end[5:]}까지 청약 가능"
                f" (주간사 {listing.lead_manager})",
            )

        if end_iso and today == end_iso and afternoon_or_later:
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "마지막날",
                f"⏰ {listing.name} 청약 마지막날입니다! 오늘까지만 청약 가능"
                f" (주간사 {listing.lead_manager})",
            )

    # 38.co.kr의 "현재 청약중" 목록은 청약종료일이 지나면 그 종목을 더 이상 보여주지
    # 않는다 — 상장일은 보통 청약종료일 며칠 뒤라, 그 사이(청약 마감~상장) 기간엔 위
    # listings 루프에 안 걸려서 상장예정일/환불일 캘린더 동기화가 끊겼다. 시트에 남아있는
    # 상장예정일/환불일이 아직 안 지난 행을 한 번 더 훑어서 보강한다.
    live_nos = {listing.no for listing in listings}
    for row in rows:
        if not row or row[0] in live_nos:
            continue
        no, name = row[0], row[1]
        lead_manager = row[6] if len(row) > 6 else ""
        listing_date_iso = row[7] if len(row) > 7 else ""
        refund_date_iso = row[8] if len(row) > 8 else ""
        if not listing_date_iso and not refund_date_iso:
            continue
        if listing_date_iso and listing_date_iso >= today and stock_calendar_id:
            calendar_client.upsert_simple_event(
                stock_calendar_id, f"ipolist{no}", f"[상장예정] {name} · {lead_manager}",
                listing_date_iso, _next_iso(listing_date_iso),
            )
        if refund_date_iso and refund_date_iso >= today and stock_calendar_id:
            calendar_client.upsert_simple_event(
                stock_calendar_id, f"iporefunddate{no}", f"[환불일] {name} · {lead_manager}",
                refund_date_iso, _next_iso(refund_date_iso),
            )
        _sync_holding_events(ws_apply, stock_calendar_id, name, listing_date_iso, refund_date_iso)

    print(f"신규 {new_count}건, 총 {len(listings)}건 확인.")


if __name__ == "__main__":
    main()

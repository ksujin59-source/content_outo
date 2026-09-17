"""38.co.kr 공모주 목록을 스크래핑해서 신규 청약 건을 감지하고, 이후 단계별 알림을 보낸다.

공모주캘린더 탭에 없는 no(38.co.kr 내부 고유번호)가 나오면 신규로 간주해 행을 추가하고
"신규 발견" 알림을 보낸다. 최초 실행 시엔 현재 청약 가능한 공모주 전체가 한꺼번에
"신규"로 잡혀 여러 건 알림이 갈 수 있는데, 이는 의도된 동작이다(지금 뭐가 열려있는지
따라잡기).

이미 추적 중인 행에 대해서는 매 실행마다 아래 네 가지를 확인해서 각각 최대 1회씩 알린다
(공모주알람로그 탭으로 중복 발송을 막는다 — ipo_listing_alert.py와 동일한 dedup 패턴):
  - 공모가확정: 확정공모가가 비어있다가 채워지는 순간
  - 일주일전: 오늘이 청약시작일 - 7일
  - 첫날: 오늘이 청약시작일 (cron이 평일 9/13/18시에 도므로 그날 첫 실행인 9시경에 나감)
  - 마지막날: 오늘이 청약종료일이면서 KST 13:50~14:10 사이 (반드시 오후 2시대를 원해서,
    ipo_listing_alert.py의 시간대 체크 패턴을 그대로 가져옴 — 이 때문에 워크플로우 cron에
    14시(KST) 슬롯이 추가돼 있어야 이 조건이 걸릴 기회가 생긴다)

상장예정일이 아직 미확정(빈 값)인 기존 행은 매 실행마다 상세 페이지를 재조회해서 보강한다.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo
from pathlib import Path

from dotenv import load_dotenv

import ipo_calendar
import sheets_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

KST = zoneinfo.ZoneInfo("Asia/Seoul")


def _to_iso(date_str: str) -> str:
    return date_str.replace(".", "-") if date_str else ""


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
    spreadsheet = sheets_client.get_spreadsheet()
    ws_calendar = sheets_client.get_or_create_worksheet(spreadsheet, "공모주캘린더")
    ws_alarm_log = sheets_client.get_or_create_worksheet(spreadsheet, "공모주알람로그")

    rows = ws_calendar.get_all_values()[1:]
    existing = {row[0]: idx for idx, row in enumerate(rows, start=2) if row}
    existing_listing_date = {row[0]: (row[7] if len(row) > 7 else "") for row in rows if row}
    existing_price = {row[0]: (row[4] if len(row) > 4 else "") for row in rows if row}

    now_kst = dt.datetime.now(KST)
    today = now_kst.date().isoformat()
    in_afternoon_window = dt.time(13, 50) <= now_kst.time() <= dt.time(14, 10)

    listings = ipo_calendar.fetch_list()
    new_count = 0

    for listing in listings:
        if listing.no not in existing:
            detail = ipo_calendar.fetch_detail(listing.no)
            ws_calendar.append_row([
                listing.no,
                listing.name,
                _to_iso(listing.subscribe_start),
                _to_iso(listing.subscribe_end),
                listing.fixed_price,
                listing.price_range,
                listing.lead_manager,
                _to_iso(detail["listing_date"]),
                _to_iso(detail["refund_date"]),
            ])
            telegram_client.send_message(
                f"🆕 새 공모주: {listing.name}\n"
                f"청약 {listing.subscribe_start}~{listing.subscribe_end[5:]}\n"
                f"희망공모가 {listing.price_range}원\n"
                f"주간사 {listing.lead_manager}"
            )
            new_count += 1
            continue

        row_idx = existing[listing.no]

        if not existing_price.get(listing.no) and listing.fixed_price:
            ws_calendar.update(f"E{row_idx}", [[listing.fixed_price]])
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "공모가확정",
                f"💰 {listing.name} 공모가 확정: {listing.fixed_price}원",
            )

        if not existing_listing_date.get(listing.no):
            detail = ipo_calendar.fetch_detail(listing.no)
            if detail["listing_date"]:
                ws_calendar.update(f"H{row_idx}:I{row_idx}", [[
                    _to_iso(detail["listing_date"]), _to_iso(detail["refund_date"]),
                ]])

        start_iso = _to_iso(listing.subscribe_start)
        end_iso = _to_iso(listing.subscribe_end)
        week_before_iso = (
            (dt.date.fromisoformat(start_iso) - dt.timedelta(days=7)).isoformat() if start_iso else ""
        )

        if week_before_iso and today == week_before_iso:
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "일주일전",
                f"📅 {listing.name} 청약 일주일 전! 청약 {listing.subscribe_start}~{listing.subscribe_end[5:]}",
            )

        if start_iso and today == start_iso:
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "첫날",
                f"🔔 {listing.name} 청약 첫날입니다! ~{listing.subscribe_end[5:]}까지 청약 가능",
            )

        if end_iso and today == end_iso and in_afternoon_window:
            _send_alert(
                ws_alarm_log, today, now_kst, listing.name, "마지막날",
                f"⏰ {listing.name} 청약 마지막날입니다! 오늘까지만 청약 가능",
            )

    print(f"신규 {new_count}건, 총 {len(listings)}건 확인.")


if __name__ == "__main__":
    main()

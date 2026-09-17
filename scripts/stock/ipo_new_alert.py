"""38.co.kr 공모주 목록을 스크래핑해서 신규 청약 건을 감지하고 텔레그램으로 알린다.

공모주캘린더 탭에 없는 no(38.co.kr 내부 고유번호)가 나오면 신규로 간주해 행을 추가하고
알림을 보낸다. 최초 실행 시엔 현재 청약 가능한 공모주 전체가 한꺼번에 "신규"로 잡혀
여러 건 알림이 갈 수 있는데, 이는 의도된 동작이다(지금 뭐가 열려있는지 따라잡기).

상장예정일이 아직 미확정(빈 값)인 기존 행은 매 실행마다 상세 페이지를 재조회해서 보강한다.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

import ipo_calendar
import sheets_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _to_iso(date_str: str) -> str:
    return date_str.replace(".", "-") if date_str else ""


def main() -> None:
    spreadsheet = sheets_client.get_spreadsheet()
    ws_calendar = sheets_client.get_or_create_worksheet(spreadsheet, "공모주캘린더")

    rows = ws_calendar.get_all_values()[1:]
    existing = {row[0]: idx for idx, row in enumerate(rows, start=2) if row}
    existing_listing_date = {row[0]: (row[7] if len(row) > 7 else "") for row in rows if row}

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
        elif not existing_listing_date.get(listing.no):
            detail = ipo_calendar.fetch_detail(listing.no)
            if detail["listing_date"]:
                row_idx = existing[listing.no]
                ws_calendar.update(f"H{row_idx}:I{row_idx}", [[
                    _to_iso(detail["listing_date"]), _to_iso(detail["refund_date"]),
                ]])

    print(f"신규 {new_count}건, 총 {len(listings)}건 확인.")


if __name__ == "__main__":
    main()

"""텔레그램으로 들어온 매매/공모주 기록 메시지를 파싱해서 구글시트에 반영한다.

지원하는 메시지 형식:
  - 매매: "매수 삼성전자 미래에셋 10 71000 반도체 업황 회복 기대" / "매도 AAPL 토스증권 5 190.5"
    (매매유형 종목명 증권사 수량 가격 [메모]) — 메모(매수 이유 등)는 선택이고 여러 단어 가능.
    같은 종목이라도 증권사별로 보유수량/평단가를 따로 집계한다.
    국내/해외 판별: 종목 토큰이 영문(알파벳/점)만이면 해외 티커, 아니면 국내 종목명.
  - 공모주 청약: "청약 바로팜 미래에셋 수진 10 18000" (종목명 증권사 신청인 수량 청약가)
  - 공모주 매도(추천): 봇이 보낸 상장일 알림("오늘/내일 OO 상장...")에 답장(reply)으로
    "수진 10 시초가" 처럼 (신청인 수량 매도가|시초가)만 보내면 됨 — 어느 종목인지는
    답장 대상 메시지에서 자동으로 읽는다.
  - 공모주 매도(직접 지정): "공모매도 바로팜 수진 시초가" 또는 "공모매도 바로팜 수진
    미래에셋 25000" (종목명 신청인 [증권사] 매도가|시초가 — 증권사는 동일인이 여러
    증권사로 청약해 모호할 때만 필요). 알림에 답장하기 애매할 때 쓰는 방식.

이 세 종류 모두 같은 텔레그램 봇(STOCK_TELEGRAM_BOT_TOKEN)의 getUpdates를 공유하므로,
반드시 이 파일 하나의 폴링 루프 안에서 처리해야 한다 — getUpdates는 offset을 넘기면
그 이전 업데이트를 다른 호출자에게 다시 돌려주지 않으므로, 별도 스크립트로 나누면
서로의 메시지를 가로채 유실시킨다.

GitHub Actions cron(process-trades.yml)이 주기 실행하는 것을 전제로 한다. 로컬 실행 시엔
같은 폴더의 .env를 자동으로 읽는다 (없으면 무시하고 이미 설정된 환경변수를 그대로 씀).
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

from dotenv import load_dotenv

import price_lookup
import sheets_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

TICKER_MAP_PATH = Path(__file__).parent / "kr_ticker_map.json"
TRADE_RE = re.compile(r"^(매수|매도)\s+(\S+)\s+(\S+)\s+(\d+)\s+([\d.]+)(?:\s+(.+))?$")
IPO_APPLY_RE = re.compile(r"^청약\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+([\d.]+)$")
IPO_SELL_PREFIX_RE = re.compile(r"^공모매도\s+(.+)$")
IPO_REPLY_SELL_RE = re.compile(r"^(\S+)\s+(\d+)\s+(시초가|[\d.]+)$")
IPO_ALERT_NAME_RE = re.compile(r"(?:오늘|내일) (\S+) 상장")

USAGE_HINT = (
    "형식을 인식하지 못했습니다.\n"
    "매매: `매수 삼성전자 미래에셋 10 71000 [메모]` / `매도 AAPL 토스증권 5 190.5`\n"
    "공모주 청약: `청약 바로팜 미래에셋 수진 10 18000`\n"
    "공모주 매도: 상장일 알림에 답장으로 `수진 10 시초가`, 또는 직접\n"
    "`공모매도 바로팜 수진 시초가`"
)


def _parse_ipo_sell(text: str) -> tuple[str, str, str | None, str] | None:
    m = IPO_SELL_PREFIX_RE.match(text)
    if not m:
        return None
    tokens = m.group(1).split()
    if len(tokens) == 3:
        name, applicant, price_token = tokens
        return name, applicant, None, price_token
    if len(tokens) == 4:
        name, applicant, broker, price_token = tokens
        return name, applicant, broker, price_token
    return None


def _find_holding_row(ws, market: str, symbol: str, broker: str):
    rows = ws.get_all_values()[1:]
    for idx, row in enumerate(rows, start=2):
        if len(row) >= 4 and row[0] == market and row[1] == symbol and row[3] == broker:
            code = row[2] if len(row) > 2 else ""
            qty = int(row[4]) if len(row) > 4 and row[4] else 0
            avg_cost = float(row[5]) if len(row) > 5 and row[5] else 0.0
            return idx, code, qty, avg_cost
    return None, "", 0, 0.0


def _apply_trade_to_holdings(ws, market: str, symbol: str, code: str, broker: str, action: str, qty: int, price: float):
    row_idx, existing_code, held_qty, avg_cost = _find_holding_row(ws, market, symbol, broker)
    code = existing_code or code

    if action == "매수":
        new_qty = held_qty + qty
        new_avg = ((held_qty * avg_cost) + (qty * price)) / new_qty if new_qty else 0.0
    else:
        new_qty = held_qty - qty
        new_avg = avg_cost
        if new_qty < 0:
            new_qty = 0

    if new_qty <= 0:
        if row_idx is not None:
            ws.delete_rows(row_idx)
        return 0, 0.0

    row_values = [market, symbol, code, broker, str(new_qty), f"{new_avg:.2f}"]
    if row_idx is not None:
        ws.update(f"A{row_idx}:F{row_idx}", [row_values])
    else:
        ws.append_row(row_values)
    return new_qty, new_avg


def _handle_ipo_apply(ws_apply, ws_calendar, today: str, match: re.Match) -> None:
    name, broker, applicant, qty_str, price_str = match.groups()
    qty = int(qty_str)
    price = float(price_str)

    listing_date = ""
    for row in ws_calendar.get_all_values()[1:]:
        if len(row) > 1 and row[1] == name:
            listing_date = row[7] if len(row) > 7 else ""
            break

    ws_apply.append_row([today, name, broker, applicant, qty, price, listing_date, "대기", "", "", ""])

    reply = f"✅ {name} 청약 기록 완료\n{broker} · {applicant} · {qty}주 @{price:,.0f}"
    reply += f"\n상장예정일 {listing_date}" if listing_date else "\n상장예정일 미정 (확정되면 자동 반영됨)"
    telegram_client.send_message(reply)


def _find_ipo_sell_candidates(ws_apply, name: str, applicant: str, broker: str | None):
    rows = ws_apply.get_all_values()[1:]
    return [
        (idx, row) for idx, row in enumerate(rows, start=2)
        if len(row) >= 8 and row[1] == name and row[3] == applicant and row[7] == "대기"
        and (broker is None or row[2] == broker)
    ]


def _finalize_ipo_sale(ws_apply, row_idx, row, today: str, price_token: str, expected_qty: int | None = None) -> None:
    name = row[1]
    apply_qty = int(row[4])
    apply_price = float(row[5])

    if price_token == "시초가":
        try:
            code = price_lookup.resolve_kr_code(name, TICKER_MAP_PATH)
            sell_price = price_lookup.fetch_kr_opening_price(code)
        except (ValueError, KeyError, IndexError) as exc:
            telegram_client.send_message(f"⚠️ {name} 시초가 조회 실패: {exc}")
            return
    else:
        sell_price = float(price_token)

    profit_pct = (sell_price - apply_price) / apply_price * 100 if apply_price else 0.0
    ws_apply.update(f"H{row_idx}:K{row_idx}", [["매도완료", f"{sell_price:.2f}", today, f"{profit_pct:.2f}"]])

    reply = f"✅ {name} 매도완료 · {sell_price:,.0f}원\n수익률 {profit_pct:+.1f}%"
    if expected_qty is not None and expected_qty != apply_qty:
        reply += f"\n⚠️ 청약수량({apply_qty}주)과 입력한 수량({expected_qty}주)이 다릅니다 — 확인해주세요."
    telegram_client.send_message(reply)


def _handle_ipo_sell(ws_apply, today: str, name: str, applicant: str, broker: str | None, price_token: str) -> None:
    candidates = _find_ipo_sell_candidates(ws_apply, name, applicant, broker)

    if not candidates:
        telegram_client.send_message(f"⚠️ '{name}' · '{applicant}' 명의로 대기중인 공모주 신청을 찾지 못했습니다.")
        return
    if len(candidates) > 1:
        brokers = ", ".join(row[2] for _, row in candidates)
        telegram_client.send_message(
            f"⚠️ '{name}' · '{applicant}' 명의로 대기중인 신청이 여러 건입니다 ({brokers}).\n"
            f"증권사를 포함해서 다시 보내주세요: 공모매도 {name} {applicant} <증권사> {price_token}"
        )
        return

    row_idx, row = candidates[0]
    _finalize_ipo_sale(ws_apply, row_idx, row, today, price_token)


def _handle_ipo_sell_reply(ws_apply, today: str, name: str, applicant: str, qty: int, price_token: str) -> None:
    candidates = _find_ipo_sell_candidates(ws_apply, name, applicant, None)

    if not candidates:
        telegram_client.send_message(f"⚠️ '{name}' · '{applicant}' 명의로 대기중인 공모주 신청을 찾지 못했습니다.")
        return
    if len(candidates) > 1:
        brokers = ", ".join(row[2] for _, row in candidates)
        telegram_client.send_message(
            f"⚠️ '{name}' · '{applicant}' 명의로 대기중인 신청이 여러 건입니다 ({brokers}).\n"
            f"증권사를 포함해서 다시 보내주세요: 공모매도 {name} {applicant} <증권사> {price_token}"
        )
        return

    row_idx, row = candidates[0]
    _finalize_ipo_sale(ws_apply, row_idx, row, today, price_token, expected_qty=qty)


def main() -> None:
    spreadsheet = sheets_client.get_spreadsheet()
    ws_trades = sheets_client.get_or_create_worksheet(spreadsheet, "거래내역")
    ws_holdings = sheets_client.get_or_create_worksheet(spreadsheet, "보유종목")
    ws_state = sheets_client.get_or_create_worksheet(spreadsheet, "상태")
    ws_ipo_apply = sheets_client.get_or_create_worksheet(spreadsheet, "공모주신청")
    ws_ipo_calendar = sheets_client.get_or_create_worksheet(spreadsheet, "공모주캘린더")

    last_update_id = int(sheets_client.get_state(ws_state, "last_update_id", "0") or "0")
    updates = telegram_client.get_updates(offset=last_update_id + 1)

    if not updates:
        print("새 메시지 없음.")
        return

    configured_chat_id = os.environ.get("STOCK_TELEGRAM_CHAT_ID", "")
    max_update_id = last_update_id
    today = dt.date.today().isoformat()

    for update in updates:
        max_update_id = max(max_update_id, update["update_id"])
        message = update.get("message")
        if not message or "text" not in message:
            continue
        if str(message["chat"]["id"]) != configured_chat_id:
            continue

        text = message["text"].strip()

        reply_to = message.get("reply_to_message") or {}
        alert_name_match = IPO_ALERT_NAME_RE.search(reply_to.get("text", ""))
        reply_sell_match = IPO_REPLY_SELL_RE.match(text) if alert_name_match else None

        if reply_sell_match:
            applicant, qty_str, price_token = reply_sell_match.groups()
            _handle_ipo_sell_reply(ws_ipo_apply, today, alert_name_match.group(1), applicant, int(qty_str), price_token)
            continue

        match = TRADE_RE.match(text)
        ipo_apply_match = IPO_APPLY_RE.match(text) if not match else None
        ipo_sell_parsed = _parse_ipo_sell(text) if not match and not ipo_apply_match else None

        if ipo_apply_match:
            _handle_ipo_apply(ws_ipo_apply, ws_ipo_calendar, today, ipo_apply_match)
            continue
        if ipo_sell_parsed:
            _handle_ipo_sell(ws_ipo_apply, today, *ipo_sell_parsed)
            continue
        if not match:
            telegram_client.send_message(USAGE_HINT)
            continue

        action, symbol, broker, qty_str, price_str, memo = match.groups()
        qty = int(qty_str)
        price = float(price_str)
        memo = memo or ""

        if price_lookup.is_overseas_ticker(symbol):
            market, code = "해외", symbol.upper()
        else:
            market = "국내"
            try:
                code = price_lookup.resolve_kr_code(symbol, TICKER_MAP_PATH)
            except ValueError as exc:
                telegram_client.send_message(f"⚠️ {exc}")
                continue

        ws_trades.append_row([today, market, symbol, broker, action, qty, price, memo])
        new_qty, new_avg = _apply_trade_to_holdings(ws_holdings, market, symbol, code, broker, action, qty, price)

        if new_qty > 0:
            reply = (
                f"✅ {symbol}({broker}) {action} {qty}주 @{price:,.0f} 기록 완료\n"
                f"보유 {new_qty}주 · 평단가 {new_avg:,.0f}"
            )
        else:
            reply = f"✅ {symbol}({broker}) {action} {qty}주 @{price:,.0f} 기록 완료\n보유 수량 0 (청산)"
        if memo:
            reply += f"\n메모: {memo}"
        telegram_client.send_message(reply)

    sheets_client.set_state(ws_state, "last_update_id", str(max_update_id))


if __name__ == "__main__":
    main()

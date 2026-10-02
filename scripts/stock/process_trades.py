"""텔레그램으로 들어온 매매/공모주/이유식 기록 메시지를 파싱해서 구글시트(+캘린더)에
반영한다.

지원하는 메시지 형식:
  - 매매: "매수 삼성전자 미래에셋 10 71000 반도체 업황 회복 기대" (매매유형 종목명 증권사
    수량 가격 [메모], 고정 순서, 메모 선택) — 1차로 이 고정 순서를 먼저 시도한다. 안
    맞으면("매수 미래에셋 10주 71000 삼성전자"처럼 순서가 섞이면) 수량에 "주"를 붙인
    걸로 대신 재시도한다 — "주"가 있어야 가격(둘 다 숫자)과 구별되기 때문에, 순서를
    섞어 보낼 땐 수량에 "주"를 꼭 붙여야 한다. 이때 종목명은 증권사/수량/가격을 뗀
    나머지 중 첫 토큰, 그 뒤는 전부 메모로 합친다(_classify_trade_tokens 참고).
    같은 종목이라도 증권사별로 보유수량/평단가를 따로 집계한다.
    국내/해외 판별: 종목 토큰이 영문(알파벳/점)만이면 해외 티커, 아니면 국내 종목명.
  - 공모주 청약: "바로팜 미래에셋 수진 10주"(종목명 증권사 신청인 수량) — 어순은 상관
    없다("수진 10주 대신증권 바로팜"도 동일하게 인식됨). 토큰을 보고 종류를 알아서
    구분하기 때문이다: "OO주"로 끝나는 토큰이 수량, KNOWN_BROKERS 목록에 있는(또는
    부분일치하는) 토큰이 증권사, 공모주캘린더 탭에 있는 종목명과 일치하는 토큰이
    종목명, 숫자/쉼표만 있는 나머지 토큰이 가격(선택), 그러고도 하나 남는 토큰이
    신청인이다(_classify_ipo_apply_tokens 참고). 가격을 안 쓰면 공모주캘린더 탭의
    확정공모가를 자동으로 찾아 채우고(아직 확정 전이면 직접 알려달라고 안내), 쓰면
    그 값을 쓴다(쉼표 허용: "18,000"). 앞/뒤에 "청약"을 붙여도(붙이지 않아도) 된다.
    토큰 분류가 애매하면(증권사를 못 알아보거나 신청인 후보가 여럿/0개면) 인식 실패로
    처리되고 USAGE_HINT가 돌아간다.
  - 공모주 청약(답장): ipo_new_alert.py가 보낸 공모주 알림(신규 발견/공모가확정/일주일전/
    첫날/마지막날 — 이 다섯 개는 전부 종목명을 담고 있다)에 답장으로 "대신증권 수진 10주"
    처럼 (증권사 신청인 수량, 어순 무관, 가격은 선택)만 보내면 종목명은 답장 대상
    메시지에서 자동으로 읽는다. 이름을 직접 안 치니 "멜콘" 대 "멜콘(구.에스앤에프솔루션)"
    처럼 38.co.kr의 구명칭 표기 차이로 매칭이 안 되는 문제도 같이 해결됨.
  - 공모주 매도(추천): 봇이 보낸 상장일 알림("오늘/내일 OO 상장...")에 답장(reply)으로
    "수진 10 시초가" 처럼 (신청인 수량 매도가|시초가, 고정 순서)만 보내면 됨 — 어느
    종목인지는 답장 대상 메시지에서 자동으로 읽는다. 순서가 섞이면("10주 수진 시초가")
    수량에 "주"를 붙인 걸로 재시도한다(어순 무관).
  - 공모주 매도(직접 지정): "공모매도 바로팜 수진 시초가" 또는 "공모매도 바로팜 수진
    미래에셋 25000" (종목명 신청인 [증권사] 매도가|시초가, 증권사는 동일인이 여러
    증권사로 청약해 모호할 때만 필요) — 어순은 상관없다. 공모주신청 탭에서 대기중인
    종목명과 일치하는 토큰, KNOWN_BROKERS에 있는 토큰, "시초가"/숫자인 토큰을 각각
    알아보고 남는 하나를 신청인으로 본다(_classify_ipo_sell_tokens 참고). 알림에
    답장하기 애매할 때 쓰는 방식.
  - 이유식 기록: "이유식 고구마, 소고기" (접두어 "이유식" + 쉼표/줄바꿈/·//로 구분한
    음식 목록). 알레르기 테스트용으로 날짜별 구글 캘린더 이벤트(하루 1개로 누적)와
    시트(이유식기록 탭)에 기록한다. 매매 형식과 겹치지 않도록 "이유식" 접두어로 구분한다
    — 이 접두어 없이 보내면 매매 형식 파싱에 실패해 사용법 안내만 돌아간다.

이 네 종류 모두 같은 텔레그램 봇(STOCK_TELEGRAM_BOT_TOKEN)의 getUpdates를 공유하므로,
반드시 이 파일 하나의 폴링 루프 안에서 처리해야 한다 — getUpdates는 offset을 넘기면
그 이전 업데이트를 다른 호출자에게 다시 돌려주지 않으므로, 별도 스크립트로 나누면
서로의 메시지를 가로채 유실시킨다. (이유식 기록도 원래는 전용 봇을 새로 만들 계획이었으나,
봇 발급 절차를 줄이기 위해 이 주식봇 챗을 그대로 재사용하기로 함 — 그래서 "이유식" 접두어로
매매 메시지와 구분한다.)

GitHub Actions cron(process-trades.yml)이 주기 실행하는 것을 전제로 한다. 로컬 실행 시엔
같은 폴더의 .env를 자동으로 읽는다 (없으면 무시하고 이미 설정된 환경변수를 그대로 씀).
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

import calendar_client
import price_lookup
import sheets_client
import telegram_client

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

SEOUL = ZoneInfo("Asia/Seoul")
TICKER_MAP_PATH = Path(__file__).parent / "kr_ticker_map.json"
TRADE_RE = re.compile(r"^(매수|매도)\s+(\S+)\s+(\S+)\s+(\d+)\s+([\d.]+)(?:\s+(.+))?$")
IPO_SELL_PREFIX_RE = re.compile(r"^공모매도\s+(.+)$")
IPO_REPLY_SELL_RE = re.compile(r"^(\S+)\s+(\d+)\s+(시초가|[\d.]+)$")
IPO_ALERT_NAME_RE = re.compile(r"(?:오늘|내일) (\S+) 상장")
IPO_SCHEDULE_ALERT_NAME_PATTERNS = [
    re.compile(r"새 공모주: (\S+)"),
    re.compile(r"(\S+) 공모가 확정"),
    re.compile(r"(\S+) 청약 일주일 전"),
    re.compile(r"(\S+) 청약 첫날입니다"),
    re.compile(r"(\S+) 청약 마지막날입니다"),
]
FOOD_RE = re.compile(r"^이유식\s+(.+)$", re.DOTALL)
FOOD_SEPARATOR_RE = re.compile(r"[,\n·/]+")
RESERVED_FIRST_WORDS = {"매수", "매도", "공모매도", "이유식"}

# 공모주 청약 메시지에서 "증권사"를 알아보기 위한 참고 목록 — 정확히 일치하거나(우선),
# 이 중 하나를 포함/이 중 하나에 포함되면(부분 일치) 그 토큰을 증권사로 본다. 어순이
# 바뀌어도(신청인/증권사/수량 순서가 섞여도) 알아서 인식하게 하려고 토큰 종류별로
# 식별하는 방식을 쓰기 때문에 필요하다.
KNOWN_BROKERS = [
    "미래에셋증권", "미래에셋", "삼성증권", "한국투자증권", "한국투자", "한투",
    "NH투자증권", "NH", "KB증권", "신한투자증권", "신한금융투자", "신한",
    "대신증권", "대신", "하나증권", "키움증권", "키움", "유진투자증권", "유진",
    "IBK투자증권", "IBK", "SK증권", "한화투자증권", "한화", "아이엠증권",
    "토스증권", "토스", "카카오페이증권", "카카오페이", "유안타증권", "유안타",
    "DB금융투자", "교보증권", "교보", "현대차증권", "현대차", "다올투자증권",
    "다올", "메리츠증권", "메리츠", "상상인증권", "상상인", "한양증권", "한양",
    "리딩투자증권", "부국증권", "부국",
]

USAGE_HINT = (
    "형식을 인식하지 못했습니다.\n"
    "매매: `매수 삼성전자 미래에셋 10 71000 [메모]` (순서가 섞이면 수량에 \"주\"를\n"
    "붙여서: `매수 미래에셋 10주 71000 삼성전자`)\n"
    "공모주 청약: `바로팜 미래에셋 수진 10주` (어순 무관, 앞/뒤에 \"청약\" 붙여도 됨,\n"
    "청약가는 자동 확인되고 직접 써도 됨), 또는 공모주 알림에 답장으로 `미래에셋 수진 10주`\n"
    "공모주 매도: 상장일 알림에 답장으로 `수진 10 시초가`, 또는 직접\n"
    "`공모매도 바로팜 수진 시초가` (둘 다 어순 무관)\n"
    "이유식 기록: `이유식 고구마, 소고기`\n"
    "전체 양식 다시 보려면: `양식`"
)

FORMAT_GUIDE_TRIGGER = "양식"

FORMAT_GUIDE = (
    "📋 사용 가능한 전체 형식\n\n"
    "📈 매매\n"
    "`매수 삼성전자 미래에셋 10 71000 [메모]`\n"
    "`매도 AAPL 토스증권 5 190.5`\n"
    "(매매유형 종목명 증권사 수량 가격 [메모]) — 순서가 섞이면 수량에 \"주\"를 붙여서\n"
    "다시 보내면 어순 상관없이 인식됨(예: `매수 미래에셋 10주 71000 삼성전자`)\n\n"
    "🆕 공모주 청약\n"
    "`바로팜 미래에셋 수진 10주` — 어순 무관(증권사/신청인/수량 순서 섞여도 됨),\n"
    "앞/뒤에 \"청약\" 붙여도 됨, 청약가 자동 확인\n"
    "`바로팜 미래에셋 수진 10주 18000` — 청약가 직접 지정(쉼표 가능, 예: 18,000)\n"
    "공모주 알림에 답장으로 `미래에셋 수진 10주` (가격 직접 써도 됨) — 종목명 자동 인식\n\n"
    "💰 공모주 매도\n"
    "상장일 알림에 답장으로 `수진 10 시초가` (또는 매도가, 어순 무관)\n"
    "`공모매도 바로팜 수진 시초가`\n"
    "`공모매도 바로팜 수진 미래에셋 25000` — 동일인 여러 증권사 청약 시 증권사 포함,\n"
    "둘 다 어순 무관(종목명/신청인/증권사/가격 순서 섞여도 됨)\n\n"
    "🍼 이유식 기록\n"
    "`이유식 고구마, 소고기` (쉼표/줄바꿈/·// 로 여러 개 구분)\n\n"
    f"이 안내는 아무 때나 `{FORMAT_GUIDE_TRIGGER}`라고 보내면 다시 볼 수 있습니다."
)


def _extract_ipo_schedule_name(alert_text: str) -> str | None:
    for pattern in IPO_SCHEDULE_ALERT_NAME_PATTERNS:
        m = pattern.search(alert_text)
        if m:
            return m.group(1)
    return None


def _strip_edge_word(text: str, word: str) -> str:
    """"청약"을 앞이나 뒤 어디에 붙여 보내도 받아주기 위한 전처리 — "멜콘 대신증권
    수진 10주 청약"처럼 뒤에 붙이는 습관이 흔해서, 맨 앞/맨 뒤에 그 단어 하나만 있으면
    떼고 나머지로 핵심 패턴을 매칭한다."""
    tokens = text.split()
    if tokens and tokens[0] == word:
        return " ".join(tokens[1:])
    if tokens and tokens[-1] == word:
        return " ".join(tokens[:-1])
    return text


def _is_broker_token(token: str) -> bool:
    return any(token in b or b in token for b in KNOWN_BROKERS)


def _classify_ipo_apply_tokens(tokens: list[str], ws_calendar=None) -> dict | None:
    """공모주 청약 메시지의 토큰을 어순 상관없이 분류한다 — "대신증권 수진 10주"든
    "수진 대신증권 10주"든 같게 인식하기 위함(사용자가 딱 정해진 어순을 외우지 않아도
    되게). 수량("OO주")과 증권사(KNOWN_BROKERS)로 먼저 확실한 토큰부터 떼어내고,
    남은 숫자 토큰은 가격(선택)으로, ws_calendar가 주어지면 공모주캘린더에 있는
    종목명과 일치하는 토큰을 종목명으로 떼어낸다. 그러고도 토큰이 정확히 1개 남아야
    그게 신청인이라고 확신할 수 있다 — 애매하면(수량/증권사를 못 찾거나, 종목명을 찾아야
    하는데 못 찾거나, 남는 토큰이 0개/2개 이상이면) None을 돌려줘서 호출부가 안내
    메시지로 다시 요청하게 한다."""
    remaining = list(tokens)

    qty = None
    for t in remaining:
        m = re.fullmatch(r"(\d+)주", t)
        if m:
            qty = int(m.group(1))
            remaining.remove(t)
            break
    if qty is None:
        return None

    price = None
    for t in remaining:
        if re.fullmatch(r"[\d,]+", t):
            price = float(t.replace(",", ""))
            remaining.remove(t)
            break

    exact_brokers = [t for t in remaining if t in KNOWN_BROKERS]
    broker = exact_brokers[0] if exact_brokers else next((t for t in remaining if _is_broker_token(t)), None)
    if broker is None:
        return None
    remaining.remove(broker)

    name = None
    if ws_calendar is not None:
        name = next((t for t in remaining if _find_ipo_calendar_row(ws_calendar, t)), None)
        if name is None:
            return None
        remaining.remove(name)

    if len(remaining) != 1:
        return None

    return {"name": name, "broker": broker, "applicant": remaining[0], "qty": qty, "price": price}


def _classify_trade_tokens(tokens: list[str]) -> dict | None:
    """매매 메시지의 "매수/매도" 뒤쪽 토큰을 어순 상관없이 분류한다 — TRADE_RE의 고정
    순서가 먼저 시도되고(기존 습관 그대로 호환), 그게 안 맞을 때만 쓰는 대체 경로다.
    수량에 "주"를 꼭 붙여야 가격(둘 다 숫자라 어순 없인 구분 불가)과 구별된다. 증권사를
    알아보고 나면 남은 토큰 중 첫 번째를 종목명으로, 그 뒤에 남는 건 전부 메모로 합친다
    (메모는 자유 문장이라 토큰 하나로 못 좁히므로 "첫 leftover=종목명, 나머지=메모"로
    단순화함)."""
    remaining = list(tokens)

    qty = None
    for t in remaining:
        m = re.fullmatch(r"(\d+)주", t)
        if m:
            qty = int(m.group(1))
            remaining.remove(t)
            break
    if qty is None:
        return None

    price = None
    for t in remaining:
        if re.fullmatch(r"[\d,]+(?:\.\d+)?", t):
            price = float(t.replace(",", ""))
            remaining.remove(t)
            break
    if price is None:
        return None

    exact_brokers = [t for t in remaining if t in KNOWN_BROKERS]
    broker = exact_brokers[0] if exact_brokers else next((t for t in remaining if _is_broker_token(t)), None)
    if broker is None:
        return None
    remaining.remove(broker)

    if not remaining:
        return None
    symbol = remaining[0]
    memo = " ".join(remaining[1:])

    return {"symbol": symbol, "broker": broker, "qty": qty, "price": price, "memo": memo}


def _parse_food_items(text: str) -> list[str]:
    parts = FOOD_SEPARATOR_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen.keys())


def _handle_food_log(ws_food, food_calendar_id: str, message: dict, food_text: str, raw_text: str) -> None:
    items = _parse_food_items(food_text)
    if not items:
        telegram_client.send_message(USAGE_HINT)
        return

    msg_dt = dt.datetime.fromtimestamp(message["date"], tz=SEOUL)
    date_str = msg_dt.date().isoformat()
    time_str = msg_dt.strftime("%H:%M")

    for item in items:
        ws_food.append_row([date_str, time_str, item, raw_text])

    unique_items = _dedupe_keep_order(items)
    calendar_client.upsert_event(food_calendar_id, date_str, unique_items, time_str)

    telegram_client.send_message(f"✅ {date_str} 이유식 기록 완료: {', '.join(unique_items)}")


def _find_pending_ipo_name(ws_apply, token: str) -> str | None:
    for row in ws_apply.get_all_values()[1:]:
        if len(row) >= 8 and row[7] == "대기" and row[1] and (row[1] == token or token in row[1] or row[1] in token):
            return row[1]
    return None


def _classify_ipo_sell_tokens(tokens: list[str], ws_apply) -> dict | None:
    """"공모매도" 뒤쪽 토큰을 어순 상관없이 분류한다. 매도가("시초가" 또는 숫자)와
    공모주신청 탭에 대기중으로 올라와 있는 종목명으로 확실한 토큰부터 떼어내고, 남은
    토큰 중 KNOWN_BROKERS에 있는 게 있으면 증권사로(동명이인이 증권사별로 여러 건
    신청했을 때만 필요, 없어도 됨), 그러고도 정확히 1개 남아야 그게 신청인이다."""
    remaining = list(tokens)

    price_token = None
    for t in remaining:
        if t == "시초가" or re.fullmatch(r"[\d,.]+", t):
            price_token = t
            remaining.remove(t)
            break
    if price_token is None:
        return None

    name = None
    for t in remaining:
        found = _find_pending_ipo_name(ws_apply, t)
        if found:
            name = found
            remaining.remove(t)
            break
    if name is None:
        return None

    broker = None
    exact = [t for t in remaining if t in KNOWN_BROKERS]
    if exact:
        broker = exact[0]
        remaining.remove(broker)
    else:
        fuzzy = next((t for t in remaining if _is_broker_token(t)), None)
        if fuzzy:
            broker = fuzzy
            remaining.remove(fuzzy)

    if len(remaining) != 1:
        return None

    return {"name": name, "applicant": remaining[0], "broker": broker, "price_token": price_token}


def _parse_ipo_sell(text: str, ws_apply) -> tuple[str, str, str | None, str] | None:
    m = IPO_SELL_PREFIX_RE.match(text)
    if not m:
        return None
    classified = _classify_ipo_sell_tokens(m.group(1).split(), ws_apply)
    if classified is None:
        return None
    return classified["name"], classified["applicant"], classified["broker"], classified["price_token"]


def _classify_ipo_reply_sell_tokens(tokens: list[str]) -> dict | None:
    """상장일 알림에 대한 답장("수진 10 시초가")의 어순이 섞인 경우의 대체 경로 —
    IPO_REPLY_SELL_RE의 고정 순서가 먼저 시도되고, 그게 안 맞을 때만 쓴다. 수량에
    "주"를 붙여야("10주") 가격과 구별되므로, 순서를 섞어 보낼 땐 "주"가 필요하다."""
    remaining = list(tokens)

    qty = None
    for t in remaining:
        m = re.fullmatch(r"(\d+)주", t)
        if m:
            qty = int(m.group(1))
            remaining.remove(t)
            break
    if qty is None:
        return None

    price_token = None
    for t in remaining:
        if t == "시초가" or re.fullmatch(r"[\d,.]+", t):
            price_token = t
            remaining.remove(t)
            break
    if price_token is None:
        return None

    if len(remaining) != 1:
        return None

    return {"applicant": remaining[0], "qty": qty, "price_token": price_token}


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


def _find_ipo_calendar_row(ws_calendar, name: str) -> list[str] | None:
    rows = ws_calendar.get_all_values()[1:]
    for row in rows:
        if len(row) > 1 and row[1] == name:
            return row
    # 38.co.kr이 종목명을 "멜콘(구.에스앤에프솔루션)"처럼 구명칭 포함 표기로 바꿔두는
    # 경우가 있어서, 정확히 일치하는 게 없으면 부분 일치(어느 한쪽이 다른 쪽을 포함)로
    # 한 번 더 찾는다.
    for row in rows:
        if len(row) > 1 and row[1] and (name in row[1] or row[1] in name):
            return row
    return None


def _handle_ipo_apply(
    ws_apply, ws_calendar, stock_calendar_id: str, today: str,
    name: str, broker: str, applicant: str, qty: int, price: float | None = None,
) -> None:
    calendar_row = _find_ipo_calendar_row(ws_calendar, name)
    listing_date = calendar_row[7] if calendar_row and len(calendar_row) > 7 else ""
    lead_manager = calendar_row[6] if calendar_row and len(calendar_row) > 6 else ""

    if price is None:
        fixed_price = calendar_row[4] if calendar_row and len(calendar_row) > 4 else ""
        try:
            price = float(fixed_price.replace(",", "")) if fixed_price and fixed_price != "-" else None
        except ValueError:
            price = None
        if price is None:
            telegram_client.send_message(
                f"⚠️ '{name}' 확정공모가를 아직 찾지 못했습니다. 가격을 직접 알려주세요:\n"
                f"청약 {name} {broker} {applicant} {qty} <청약가>"
            )
            return

    ws_apply.append_row([today, name, broker, applicant, qty, price, listing_date, "대기", "", "", ""])
    event_summary = f"[청약] {name} {broker} {price:,.0f}원"
    if lead_manager:
        event_summary += f" (주간사 {lead_manager})"
    calendar_client.create_event(stock_calendar_id, today, event_summary)

    reply = f"✅ {name} 청약 기록 완료\n{broker} · {applicant} · {qty}주 @{price:,.0f}"
    if lead_manager:
        reply += f"\n주간사 {lead_manager}"
    reply += f"\n상장예정일 {listing_date}" if listing_date else "\n상장예정일 미정 (확정되면 자동 반영됨)"
    telegram_client.send_message(reply)


def _find_ipo_sell_candidates(ws_apply, name: str, applicant: str, broker: str | None):
    rows = ws_apply.get_all_values()[1:]
    return [
        (idx, row) for idx, row in enumerate(rows, start=2)
        if len(row) >= 8 and row[1] == name and row[3] == applicant and row[7] == "대기"
        and (broker is None or row[2] == broker)
    ]


def _finalize_ipo_sale(ws_apply, ws_calendar, stock_calendar_id: str, row_idx, row, today: str, price_token: str, expected_qty: int | None = None) -> None:
    name = row[1]
    broker = row[2]
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

    calendar_row = _find_ipo_calendar_row(ws_calendar, name)
    lead_manager = calendar_row[6] if calendar_row and len(calendar_row) > 6 else ""

    profit_pct = (sell_price - apply_price) / apply_price * 100 if apply_price else 0.0
    profit_amount = (sell_price - apply_price) * apply_qty
    ws_apply.update(f"H{row_idx}:K{row_idx}", [["매도완료", f"{sell_price:.2f}", today, f"{profit_pct:.2f}"]])
    event_summary = f"[상장] {name} {broker} {apply_qty}주 {sell_price:,.0f}원"
    if lead_manager:
        event_summary += f" (주간사 {lead_manager})"
    calendar_client.create_event(
        stock_calendar_id, today, event_summary,
        f"수익 {profit_amount:,.0f}원 ({profit_pct:+.1f}%)",
    )

    reply = f"✅ {name} 매도완료 · {sell_price:,.0f}원\n수익률 {profit_pct:+.1f}%"
    if lead_manager:
        reply += f"\n주간사 {lead_manager}"
    if expected_qty is not None and expected_qty != apply_qty:
        reply += f"\n⚠️ 청약수량({apply_qty}주)과 입력한 수량({expected_qty}주)이 다릅니다 — 확인해주세요."
    telegram_client.send_message(reply)


def _handle_ipo_sell(ws_apply, ws_calendar, stock_calendar_id: str, today: str, name: str, applicant: str, broker: str | None, price_token: str) -> None:
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
    _finalize_ipo_sale(ws_apply, ws_calendar, stock_calendar_id, row_idx, row, today, price_token)


def _handle_ipo_sell_reply(ws_apply, ws_calendar, stock_calendar_id: str, today: str, name: str, applicant: str, qty: int, price_token: str) -> None:
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
    _finalize_ipo_sale(ws_apply, ws_calendar, stock_calendar_id, row_idx, row, today, price_token, expected_qty=qty)


def main() -> None:
    spreadsheet = sheets_client.get_spreadsheet()
    ws_trades = sheets_client.get_or_create_worksheet(spreadsheet, "거래내역")
    ws_holdings = sheets_client.get_or_create_worksheet(spreadsheet, "보유종목")
    ws_state = sheets_client.get_or_create_worksheet(spreadsheet, "상태")
    ws_ipo_apply = sheets_client.get_or_create_worksheet(spreadsheet, "공모주신청")
    ws_ipo_calendar = sheets_client.get_or_create_worksheet(spreadsheet, "공모주캘린더")
    ws_food = sheets_client.get_or_create_worksheet(spreadsheet, "이유식기록")

    last_update_id = int(sheets_client.get_state(ws_state, "last_update_id", "0") or "0")
    updates = telegram_client.get_updates(offset=last_update_id + 1)

    if not updates:
        print("새 메시지 없음.")
        return

    configured_chat_id = os.environ.get("STOCK_TELEGRAM_CHAT_ID", "")
    food_calendar_id = os.environ.get("FOOD_GOOGLE_CALENDAR_ID", "")
    stock_calendar_id = os.environ.get("STOCK_GOOGLE_CALENDAR_ID", "")
    max_update_id = last_update_id
    today = dt.date.today().isoformat()

    for update in updates:
        # 메시지 하나 처리가 끝날 때마다 바로 offset을 커밋한다(finally). 예전엔 배치
        # 전체를 다 처리한 뒤 한 번만 저장해서, 뒤쪽 메시지 하나가 처리 중 오류로 죽으면
        # 다음 실행 때 이미 성공한 앞쪽 메시지들까지 통째로 재처리되어 시트/캘린더에 같은
        # 내용이 반복 기록되는 문제가 있었다. 이제는 실패해도 그 메시지 하나만 건너뛰고,
        # 텔레그램으로 오류를 알려서 재처리 루프에 빠지지 않게 한다.
        try:
            message = update.get("message")
            if not message or "text" not in message:
                continue
            if str(message["chat"]["id"]) != configured_chat_id:
                continue

            text = message["text"].strip()

            if text == FORMAT_GUIDE_TRIGGER:
                telegram_client.send_message(FORMAT_GUIDE)
                continue

            food_match = FOOD_RE.match(text)
            if food_match:
                _handle_food_log(ws_food, food_calendar_id, message, food_match.group(1), text)
                continue

            reply_to = message.get("reply_to_message") or {}
            reply_to_text = reply_to.get("text", "")
            alert_name_match = IPO_ALERT_NAME_RE.search(reply_to_text)
            reply_sell_match = IPO_REPLY_SELL_RE.match(text) if alert_name_match else None
            reply_sell_parsed = None
            if reply_sell_match:
                applicant, qty_str, price_token = reply_sell_match.groups()
                reply_sell_parsed = {"applicant": applicant, "qty": int(qty_str), "price_token": price_token}
            elif alert_name_match:
                reply_sell_parsed = _classify_ipo_reply_sell_tokens(text.split())

            if reply_sell_parsed:
                _handle_ipo_sell_reply(
                    ws_ipo_apply, ws_ipo_calendar, stock_calendar_id, today, alert_name_match.group(1),
                    reply_sell_parsed["applicant"], reply_sell_parsed["qty"], reply_sell_parsed["price_token"],
                )
                continue

            schedule_alert_name = _extract_ipo_schedule_name(reply_to_text)
            reply_apply_tokens = _strip_edge_word(text, "청약").split()
            reply_apply_parsed = (
                _classify_ipo_apply_tokens(reply_apply_tokens)
                if schedule_alert_name and reply_apply_tokens and reply_apply_tokens[0] not in RESERVED_FIRST_WORDS
                else None
            )

            if reply_apply_parsed:
                _handle_ipo_apply(
                    ws_ipo_apply, ws_ipo_calendar, stock_calendar_id, today, schedule_alert_name,
                    reply_apply_parsed["broker"], reply_apply_parsed["applicant"],
                    reply_apply_parsed["qty"], reply_apply_parsed["price"],
                )
                continue

            trade_match = TRADE_RE.match(text)
            if trade_match:
                action, symbol, broker, qty_str, price_str, memo = trade_match.groups()
                trade_parsed = {
                    "action": action, "symbol": symbol, "broker": broker,
                    "qty": int(qty_str), "price": float(price_str), "memo": memo or "",
                }
            else:
                trade_tokens = text.split()
                trade_parsed = None
                if trade_tokens and trade_tokens[0] in ("매수", "매도"):
                    classified = _classify_trade_tokens(trade_tokens[1:])
                    if classified:
                        trade_parsed = {"action": trade_tokens[0], **classified}

            apply_tokens = _strip_edge_word(text, "청약").split()
            ipo_apply_parsed = (
                _classify_ipo_apply_tokens(apply_tokens, ws_calendar=ws_ipo_calendar)
                if not trade_parsed and apply_tokens and apply_tokens[0] not in RESERVED_FIRST_WORDS
                else None
            )
            ipo_sell_parsed = (
                _parse_ipo_sell(text, ws_ipo_apply) if not trade_parsed and not ipo_apply_parsed else None
            )

            if ipo_apply_parsed:
                _handle_ipo_apply(
                    ws_ipo_apply, ws_ipo_calendar, stock_calendar_id, today, ipo_apply_parsed["name"],
                    ipo_apply_parsed["broker"], ipo_apply_parsed["applicant"],
                    ipo_apply_parsed["qty"], ipo_apply_parsed["price"],
                )
                continue
            if ipo_sell_parsed:
                _handle_ipo_sell(ws_ipo_apply, ws_ipo_calendar, stock_calendar_id, today, *ipo_sell_parsed)
                continue
            if not trade_parsed:
                telegram_client.send_message(USAGE_HINT)
                continue

            action = trade_parsed["action"]
            symbol = trade_parsed["symbol"]
            broker = trade_parsed["broker"]
            qty = trade_parsed["qty"]
            price = trade_parsed["price"]
            memo = trade_parsed["memo"]

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
            calendar_client.create_event(
                stock_calendar_id, today, f"[{action}] {symbol} {qty}주 {price:,.0f}원",
                f"총 수량 {new_qty}주, 평단가 {new_avg:,.0f}원",
            )

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
        except Exception as exc:
            telegram_client.send_message(f"⚠️ 메시지 처리 중 오류가 발생했습니다: {exc}")
        finally:
            max_update_id = max(max_update_id, update["update_id"])
            sheets_client.set_state(ws_state, "last_update_id", str(max_update_id))


if __name__ == "__main__":
    main()

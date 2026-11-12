"""38.co.kr에서 공모주 청약일정을 스크래핑하는 공용 모듈.

이 사이트는 EUC-KR 인코딩이라 response.content.decode('euc-kr')로 읽어야 하고,
레거시 SSL 설정 탓에 기본 requests로는 SSLV3_ALERT_HANDSHAKE_FAILURE로 접속이
막힌다 — SECLEVEL=1로 낮춘 커스텀 어댑터가 필요하다 (2026-09 기준 실제 호출로 확인).
비공식 스크래핑이라 사이트 구조가 바뀌면 이 모듈도 함께 손봐야 한다.
"""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter

LIST_URL = "https://www.38.co.kr/html/fund/?o=k"
DETAIL_URL = "https://www.38.co.kr/html/fund/?o=v&no={no}&l=&page=1"
_UA = "Mozilla/5.0 (compatible; ipo-calendar-bot/1.0)"

_LIST_ROW_RE = re.compile(
    r"no=(?P<no>\d+)&amp;l=&amp;page=1\"><font color='#0066CC'>(?P<name>[^<]+)</font></a></td>\s*"
    r"<td>\s*(?P<start>\d{4}\.\d{2}\.\d{2})~(?P<end_md>\d{2}\.\d{2})\s*</td>\s*"
    r"<td align='center'>(?P<fixed>[^<]*)</td>\s*"
    r"<td align='center'>(?P<price_range>[^<]*)</td>\s*"
    r"<td align='center'>[^<]*</td>\s*"
    r"<td>(?P<lead>[^<]*)</td>"
)


@dataclass
class IpoListing:
    no: str
    name: str
    subscribe_start: str
    subscribe_end: str
    fixed_price: str
    price_range: str
    lead_manager: str


class _LegacySSLAdapter(HTTPAdapter):
    """38.co.kr의 레거시 SSL 설정과 핸드셰이크하려면 보안 레벨을 낮춰야 한다."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _LegacySSLAdapter())
    session.headers.update({"User-Agent": _UA})
    return session


def fetch_list() -> list[IpoListing]:
    resp = _session().get(LIST_URL, timeout=15)
    resp.raise_for_status()
    html = resp.content.decode("euc-kr", errors="replace")

    listings = []
    for m in _LIST_ROW_RE.finditer(html):
        start = m.group("start")
        end_md = m.group("end_md")
        end_full = f"{start[:4]}.{end_md}" if end_md else start
        listings.append(
            IpoListing(
                no=m.group("no"),
                name=m.group("name").strip(),
                subscribe_start=start,
                subscribe_end=end_full,
                fixed_price=m.group("fixed").strip(),
                price_range=m.group("price_range").strip(),
                lead_manager=m.group("lead").strip(),
            )
        )
    return listings


def _extract_field(html: str, label: str) -> str:
    pattern = re.compile(
        re.escape(label) + r"</td>\s*<td[^>]*>\s*(?:&nbsp;)*\s*([^<]*)</td>",
    )
    m = pattern.search(html)
    return m.group(1).strip() if m else ""


def _extract_rate_field(html: str, label: str) -> str:
    """기관경쟁률/의무보유확약처럼 "수요예측결과" 미니테이블에 있는 값 — 라벨이
    <font>로 감싸여 있고 그 다음 <td>가 값이라 _extract_field의 패턴과 다르다.
    수요예측이 아직 안 끝난 경우 값이 비어있거나 "0.00%"로 나올 수 있다."""
    pattern = re.compile(
        re.escape(label) + r"</font>&nbsp;\s*</td>\s*<td[^>]*>\s*([^<]*?)\s*</td>",
    )
    m = pattern.search(html)
    if not m:
        return ""
    value = m.group(1).strip()
    return "" if value in ("", "0.00%", "-") else value


def _extract_float_ratio(html: str) -> str:
    """"공모후 유통가능 물량" 표의 맨 아래 합계 행에서 유통가능물량 비율(%)을 뽑는다.
    이 표는 투자설명서 내용을 그대로 붙여넣은 자유 서식이라 회사마다 행 구성(최대주주,
    5%이상주주 등 세부 카테고리 수)이 다를 수 있다 — 그래서 "합계" 행 전체를 통째로
    찾은 뒤, 그 안의 숫자%를 전부 뽑아서 마지막 값(공모전/공모후/매각제한물량 다음에
    나오는 네 번째 비율 = 유통가능물량)을 취한다. 아직 투자설명서가 안 올라온
    종목(청약 시작 전)은 이 표 자체가 없어서 빈 문자열을 반환한다."""
    section_m = re.search(
        r"공모후\s*유통가능\s*물량(.*?)(?=<P><FONT color=\"#3366ff\"|\Z)", html, re.S,
    )
    if not section_m:
        return ""
    rows = re.findall(r"합계.*?</TR>", section_m.group(1), re.S | re.I)
    if not rows:
        return ""
    percents = re.findall(r"(\d+\.\d+)%", rows[-1])
    return f"{percents[-1]}%" if percents else ""


def fetch_detail(no: str) -> dict[str, str]:
    """상세 페이지에서 상장예정일/환불일과(공모주 분석용) 기관경쟁률/의무보유확약비율/
    유통가능물량비율을 보강 조회한다. 전부 수요예측·투자설명서가 공개된 뒤에야 채워지는
    값이라, 마감 직후거나 아직 청약이 시작되지 않은 경우 빈 문자열일 수 있다."""
    resp = _session().get(DETAIL_URL.format(no=no), timeout=15)
    resp.raise_for_status()
    html = resp.content.decode("euc-kr", errors="replace")

    return {
        "listing_date": _extract_field(html, "상장일"),
        "refund_date": _extract_field(html, "환불일"),
        "institutional_rate": _extract_rate_field(html, "기관경쟁률"),
        "lockup_ratio": _extract_rate_field(html, "의무보유확약"),
        "float_ratio": _extract_float_ratio(html),
    }


if __name__ == "__main__":
    for listing in fetch_list():
        print(listing)

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


def fetch_detail(no: str) -> dict[str, str]:
    """상세 페이지에서 상장예정일/환불일을 보강 조회한다. 마감 직후엔 상장일이
    아직 미확정이라 빈 문자열일 수 있다."""
    resp = _session().get(DETAIL_URL.format(no=no), timeout=15)
    resp.raise_for_status()
    html = resp.content.decode("euc-kr", errors="replace")

    return {
        "listing_date": _extract_field(html, "상장일"),
        "refund_date": _extract_field(html, "환불일"),
    }


if __name__ == "__main__":
    for listing in fetch_list():
        print(listing)

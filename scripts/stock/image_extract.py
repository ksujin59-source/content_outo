"""텔레그램으로 받은 증권사 앱 스크린샷에서 공모주(IPO) 정보를 OCR로 추출한다.

과금 없는 방식을 원해서 비전 LLM 대신 Tesseract(무료 오픈소스 OCR)를 쓴다. 증권사마다
화면 레이아웃이 다 달라서 좌표 기반 템플릿은 쓸 수 없지만, "공모가", "상장예정일",
"환불일" 같은 라벨 텍스트는 어느 증권사 앱이든 대체로 그대로 쓰기 때문에, 화면 전체를
OCR로 텍스트화한 뒤 그 라벨 주변에서 값을 정규식으로 찾는 방식으로 레이아웃 독립성을
확보한다. 종목명은 라벨이 일정치 않아 정규식만으론 특정하기 어려워서, 호출부가 넘겨주는
"현재 추적 중인 종목명 목록"(공모주캘린더 탭)과 OCR 텍스트를 대조해서 찾는다.

전처리가 인식률을 크게 좌우한다 (2026-10-08 실제 스크린샷으로 확인):
  - 원본 그대로 OCR → 거의 다 깨짐(의미 없는 한 글자들만 나옴)
  - 2배 확대 + 흑백 변환 + `--psm 6`(균일한 텍스트 블록 가정) → 라벨/숫자/날짜 거의
    정확하게 인식됨
한글 OCR은 음절 사이에 공백이 섞여 나오는 경우가 많아서("공 모 가"), 라벨/종목명을
찾기 전에 전체 텍스트에서 공백을 다 제거하고 비교한다. 가격 숫자는 "90,500원"의
"원"이 깨져서 숫자처럼 보이는 글자로 오인식되는 경우가 있어("90,5002!"), 쉼표로 세
자리씩 끊어지는 형태(`\\d{1,3}(?:,\\d{3})+`)만 가격으로 인정해 그런 잡음이 숫자 뒤에
붙지 않게 한다.

로컬에서 쓰려면 Tesseract 본체 설치가 추가로 필요하다(Windows: UB-Mannheim 빌드 +
한국어 데이터, Mac: `brew install tesseract tesseract-lang`, 우분투: `apt-get install
tesseract-ocr tesseract-ocr-kor` — GitHub Actions 워크플로우에 이미 추가해둠).
"""

from __future__ import annotations

import io
import re

import pytesseract
from PIL import Image

_LABEL_PATTERNS: dict[str, list[str]] = {
    "price": [r"공모가", r"확정공모가"],
    "listing_date": [r"상장예정일", r"입고.?상장.?일", r"상장일"],
    "refund_date": [r"환불일"],
    "allotment_qty": [r"배정주수", r"배정수량"],
}

_DATE_RE = re.compile(r"(\d{4})[.\-년](\d{1,2})[.\-월](\d{1,2})")
_PRICE_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
_SMALL_NUMBER_RE = re.compile(r"\d{1,4}")


def _ocr_text(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes)).convert("L")
    image = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
    return pytesseract.image_to_string(image, lang="kor+eng", config="--psm 6")


def _find_after_label(compact_text: str, label_patterns: list[str], value_re: re.Pattern) -> str | None:
    for label in label_patterns:
        m = re.search(label, compact_text)
        if not m:
            continue
        window = compact_text[m.end():m.end() + 30]
        value_m = value_re.search(window)
        if value_m:
            return value_m.group(0)
    return None


def _find_date_after_label(compact_text: str, label_patterns: list[str]) -> str | None:
    for label in label_patterns:
        m = re.search(label, compact_text)
        if not m:
            continue
        window = compact_text[m.end():m.end() + 30]
        date_m = _DATE_RE.search(window)
        if date_m:
            year, month, day = date_m.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def extract_ipo_info(image_bytes: bytes, known_names: list[str]) -> dict | None:
    """OCR 텍스트에서 공모주 정보를 뽑는다. known_names(공모주캘린더 탭의 종목명들)와
    대조해서 종목명을 못 찾으면 전체를 실패로 본다(어느 종목 건인지 모르면 아무것도
    업데이트할 수 없으므로) — None을 돌려줘서 호출부가 안내 메시지를 보내게 한다."""
    text = _ocr_text(image_bytes)
    compact = re.sub(r"\s+", "", text)

    name = next((n for n in known_names if n and n in compact), None)
    if name is None:
        return None

    price = _find_after_label(compact, _LABEL_PATTERNS["price"], _PRICE_RE)
    if price:
        price = price.replace(",", "")

    allotment_qty = _find_after_label(compact, _LABEL_PATTERNS["allotment_qty"], _SMALL_NUMBER_RE)

    return {
        "name": name,
        "price": price,
        "listing_date": _find_date_after_label(compact, _LABEL_PATTERNS["listing_date"]),
        "refund_date": _find_date_after_label(compact, _LABEL_PATTERNS["refund_date"]),
        "allotment_qty": allotment_qty,
        "broker": None,
    }

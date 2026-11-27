"""공통 HTTP 요청, 인코딩 자동 판별, HTML 텍스트 정제 및 한국 주소·전화번호 패턴 스캐너."""

from __future__ import annotations

import html
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 한국 전화번호(휴대폰, 지역번호, 안심번호 0507 등) 정규식
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:01[016789]|02|0[3-6][1-5]|050\d|070)\s*[-.)]\s*\d{3,4}\s*[-.]\s*\d{4}(?!\d)"
)

# 한국 주소 단서(~시/군/구 + ~읍/면/동/리/로/길 + 선택적 번지) 정규식
ADDRESS_CLUE_PATTERN = re.compile(
    r"(?:(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
    r"[가-힣]*\s+)?"
    r"[가-힣]{1,8}(?:시|군|구)\s+"
    r"[가-힣0-9]{1,10}(?:읍|면|동|로|길)"
    r"(?:\s+[가-힣0-9]{1,10}(?:리|로|길|번길))?"
    r"(?:\s+(?:산\s*)?\d+(?:-\d+)?)?"
)


class _TextExtractor(HTMLParser):
    """스크립트/스타일을 제거하고 읽기 좋은 본문 텍스트와 링크를 추출하는 경량 파서."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "header", "footer", "nav"}
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "article", "section", "td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.chunks: List[str] = []
        self.title_chunks: List[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
        elif t == "title":
            self._in_title = True
        elif t in self.BLOCK_TAGS and self._skip_depth == 0:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif t == "title":
            self._in_title = False
        elif t in self.BLOCK_TAGS and self._skip_depth == 0:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_chunks.append(data)
        if self._skip_depth == 0:
            cleaned = data.strip()
            if cleaned:
                self.chunks.append(cleaned + " ")


def decode_bytes(raw: bytes, content_type: str = "") -> str:
    """UTF-8 / EUC-KR / CP949 인코딩을 안전하게 판별하여 문자열로 변환한다."""
    charset_match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    candidates = []
    if charset_match:
        candidates.append(charset_match.group(1))
    candidates.extend(["utf-8", "cp949", "euc-kr"])

    for enc in candidates:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def http_get_text(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 12.0,
) -> Tuple[int, str, str]:
    """GET 요청을 수행하고 (status_code, decoded_body, final_url)을 반환한다."""
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    req = urllib.request.Request(url, headers=merged_headers, method="GET")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, decode_bytes(raw, ctype), resp.geturl()
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, decode_bytes(raw, ctype), url


def http_post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 25.0,
) -> Tuple[int, Dict[str, Any], str]:
    """JSON POST 요청을 수행하고 (status_code, parsed_json, raw_text)을 반환한다."""
    merged_headers = dict(DEFAULT_HEADERS)
    merged_headers["Content-Type"] = "application/json; charset=utf-8"
    if headers:
        merged_headers.update(headers)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=merged_headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw_text = decode_bytes(resp.read(), resp.headers.get("Content-Type", "utf-8"))
            try:
                return resp.status, json.loads(raw_text), raw_text
            except json.JSONDecodeError:
                return resp.status, {}, raw_text
    except urllib.error.HTTPError as exc:
        raw_text = decode_bytes(exc.read() if exc.fp else b"", "utf-8")
        try:
            return exc.code, json.loads(raw_text), raw_text
        except json.JSONDecodeError:
            return exc.code, {}, raw_text


def strip_html_tags(text: str) -> str:
    """네이버 검색 API의 <b> 태그 및 HTML 엔티티를 제거한다."""
    if not text:
        return ""
    without_tags = re.sub(r"<[^>]+>", "", text)
    return html.unescape(without_tags).strip()


def html_to_clean_text(raw_html: str) -> Tuple[str, str]:
    """HTML 문자열에서 (문서 제목, 정제된 본문 텍스트)를 추출한다."""
    parser = _TextExtractor()
    parser.feed(raw_html)
    title = re.sub(r"\s+", " ", "".join(parser.title_chunks)).strip()
    body = "".join(parser.chunks)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return title, body


def extract_korean_clues_from_text(text: str) -> Dict[str, List[str]]:
    """본문 텍스트에서 전화번호 후보와 한국 주소(시군구·읍면리·번지) 후보를 자동 스캔한다."""
    phones: List[str] = []
    for match in PHONE_PATTERN.findall(text):
        normalized = re.sub(r"[\s.)]+", "-", match).strip("-")
        normalized = re.sub(r"-{2,}", "-", normalized)
        if normalized not in phones:
            phones.append(normalized)

    addresses: List[str] = []
    for match in ADDRESS_CLUE_PATTERN.findall(text):
        cleaned = re.sub(r"\s+", " ", match).strip()
        if len(cleaned) >= 5 and cleaned not in addresses:
            addresses.append(cleaned)

    return {
        "phones": phones[:12],
        "address_candidates": addresses[:15],
    }

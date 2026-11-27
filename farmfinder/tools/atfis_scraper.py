"""aT FIS 식품산업통계정보 생산자정보 조회 도구 (atfis.or.kr/home/producer.do 실시간 조회 + 상세페이지 대표자 추출 + 로컬 CSV)."""

from __future__ import annotations

import csv
import io
import re
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import DATA_DIR
from .http_client import decode_bytes, http_get_text

ATFIS_LIST_URL = "https://www.atfis.or.kr/home/producer.do"


class _AtfisTableParser(HTMLParser):
    """atfis.or.kr/home/producer.do 목록 테이블과 상세 링크(act=detail&id=...)를 파싱한다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_tr = False
        self.in_cell = False
        self.current_row: List[str] = []
        self.current_cell: List[str] = []
        self.current_link: str = ""
        self.rows: List[Tuple[List[str], str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t == "tr":
            self.in_tr = True
            self.current_row = []
            self.current_link = ""
        elif t in ("td", "th") and self.in_tr:
            self.in_cell = True
            self.current_cell = []
        elif t == "a" and self.in_tr:
            href = dict(attrs).get("href") or ""
            if "producer.do" in href or "act=detail" in href or "id=C_" in href:
                self.current_link = href

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self.in_cell:
            self.in_cell = False
            cell_text = re.sub(r"\s+", " ", "".join(self.current_cell)).strip()
            self.current_row.append(cell_text)
        elif t == "tr" and self.in_tr:
            self.in_tr = False
            if len(self.current_row) >= 4:
                self.rows.append((self.current_row, self.current_link))

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def _fetch_atfis_detail(detail_href: str) -> Dict[str, str]:
    """상세 페이지(producer.do?act=detail&id=C_...)에서 대표자명·인증정보·구성원수를 추가로 가져온다."""
    if not detail_href:
        return {}
    detail_url = urllib.parse.urljoin(ATFIS_LIST_URL, detail_href)
    try:
        status, raw_html, _ = http_get_text(detail_url, timeout=10.0)
        if status != 200 or not raw_html:
            return {"detail_url": detail_url}
    except Exception:
        return {"detail_url": detail_url}

    parser = _AtfisTableParser()
    parser.feed(raw_html)
    info: Dict[str, str] = {"detail_url": detail_url}
    for cells, _ in parser.rows:
        for idx in range(0, len(cells) - 1, 2):
            k = cells[idx].strip()
            v = cells[idx + 1].strip()
            if k in ("대표자명", "소재지", "전화번호", "인증정보", "구성원", "인증기간") and v:
                info[k] = v
    return info


def _search_local_atfis_csv(keyword: str, item_name: str, region: str, limit: int = 15) -> List[Dict[str, str]]:
    """사용자가 aT FIS에서 엑셀/CSV를 내려받아 data/ 폴더에 넣은 경우 로컬 즉시 검색을 수행한다."""
    terms = [t.strip() for t in [keyword, item_name, region] if t and t.strip()]
    if not terms:
        return []

    hits: List[Dict[str, str]] = []
    for path in sorted(DATA_DIR.glob("*atfis*.csv")) + sorted(DATA_DIR.glob("*생산자*.csv")):
        if not path.is_file():
            continue
        try:
            text = decode_bytes(path.read_bytes())
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                joined = " ".join(str(v or "") for v in row.values())
                if all(t in joined for t in terms):
                    hits.append({str(k): str(v or "").strip() for k, v in row.items()})
                    if len(hits) >= limit:
                        return hits
        except Exception:
            continue
    return hits


def search_atfis(keyword: str = "", item_name: str = "", region: str = "") -> Dict[str, Any]:
    """aT FIS 식품산업통계정보 생산자정보(법인·농협·단체 소재지, 전화번호, 대표자명)를 조회한다."""
    local_hits = _search_local_atfis_csv(keyword, item_name, region)

    # aT FIS 목록 페이지 검색 파라미터 조합
    params: Dict[str, Any] = {
        "pageUnit": 50,
        "pageIndex": 1,
    }
    if keyword.strip():
        params["searchKeyword"] = keyword.strip()
        params["farmNm"] = keyword.strip()
    if item_name.strip():
        params["itemNm"] = item_name.strip()
    if region.strip():
        params["areaNm"] = region.strip()

    query_url = f"{ATFIS_LIST_URL}?{urllib.parse.urlencode(params)}"
    try:
        status, raw_html, _ = http_get_text(query_url, timeout=14.0)
    except Exception as exc:
        return {
            "ok": bool(local_hits),
            "local_matches": local_hits,
            "error": f"aT FIS 실시간 페이지 접속 실패: {exc}",
            "source_url": ATFIS_LIST_URL,
        }

    if status != 200 or not raw_html:
        return {
            "ok": bool(local_hits),
            "local_matches": local_hits,
            "status": status,
            "source_url": query_url,
        }

    parser = _AtfisTableParser()
    parser.feed(raw_html)

    filter_terms = [t.strip() for t in [keyword, item_name, region] if t and t.strip()]
    web_matches: List[Dict[str, str]] = []

    for cells, href in parser.rows:
        if cells[0] == "조직유형" or "농가명" in cells[1]:
            continue
        row_obj = {
            "org_type": cells[0],
            "farm_name": cells[1],
            "crop_item": cells[2] if len(cells) > 2 else "",
            "address": cells[3] if len(cells) > 3 else "",
            "phone": cells[4] if len(cells) > 4 else "",
            "source_url": urllib.parse.urljoin(ATFIS_LIST_URL, href) if href else query_url,
        }
        joined_row = " ".join(row_obj.values())
        if filter_terms and not any(term in joined_row for term in filter_terms):
            continue
        web_matches.append((row_obj, href))

    # 상위 4건에 대해서는 상세 페이지(act=detail)까지 열람해 대표자명·인증정보 보강
    enriched: List[Dict[str, str]] = []
    for idx, (row_obj, href) in enumerate(web_matches[:12]):
        if idx < 4 and href:
            detail = _fetch_atfis_detail(href)
            if detail.get("대표자명"):
                row_obj["representative"] = detail["대표자명"]
            if detail.get("인증정보"):
                row_obj["cert_info"] = detail["인증정보"]
            if detail.get("전화번호") and detail["전화번호"] != "-":
                row_obj["phone"] = detail["전화번호"]
            if detail.get("detail_url"):
                row_obj["source_url"] = detail["detail_url"]
        enriched.append(row_obj)

    return {
        "ok": True,
        "query_url": query_url,
        "web_matches": enriched,
        "local_matches": local_hits,
    }

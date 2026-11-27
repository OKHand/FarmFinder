"""국립농산물품질관리원 GAP 인증정보 OpenAPI + 로컬 CSV 조회 도구."""

from __future__ import annotations

import csv
import io
import json
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

from ..config import DATA_DIR, Settings
from .http_client import decode_bytes, http_get_text


def _normalize_service_key(raw_key: str) -> str:
    """공공데이터포털 일반 인증키는 Encoding/Decoding 두 형태가 있으므로 안전하게 URL 인코딩한다."""
    if "%" in raw_key:
        return raw_key.strip()
    return urllib.parse.quote(raw_key.strip(), safe="")


def _parse_gap_response(body: str) -> List[Dict[str, str]]:
    """JSON 또는 XML 형태의 GAP OpenAPI 응답을 표준 딕셔너리 리스트로 파싱한다."""
    text = body.strip()
    if not text:
        return []

    # 1. JSON 파싱 시도
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
            # data.go.kr 표준 구조 탐색
            items = (
                data.get("response", {}).get("body", {}).get("items", {})
                or data.get("body", {}).get("items", {})
                or data.get("data")
                or data.get("items")
                or []
            )
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
            if isinstance(items, list):
                return [{str(k): str(v or "").strip() for k, v in row.items()} for row in items if isinstance(row, dict)]
        except json.JSONDecodeError:
            pass

    # 2. XML 파싱 시도
    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
            rows: List[Dict[str, str]] = []
            for item_el in root.findall(".//item") or root.findall(".//row"):
                row_dict = {child.tag: (child.text or "").strip() for child in item_el}
                if row_dict:
                    rows.append(row_dict)
            return rows
        except ET.ParseError:
            pass

    return []


def _format_gap_item(raw: Dict[str, str]) -> Dict[str, str]:
    """영문/국문 필드명을 모두 포괄해 핵심 GAP 인증 농가·단체 정보를 추출한다."""
    def pick(*keys: str) -> str:
        for k in keys:
            for rk, rv in raw.items():
                if rk.lower() == k.lower() and rv:
                    return rv
        return ""

    return {
        "cert_no": pick("certNo", "cert_no", "인증번호", "CERT_NO"),
        "group_name": pick("prdcrGrpNm", "prdcr_grp_nm", "생산자그룹명", "생산자명", "인증농가", "GRP_NM"),
        "representative": pick("rprsntvNm", "rprsntv_nm", "대표자명", "생산자", "RPRSNTV_NM"),
        "item_name": pick("itemNm", "item_nm", "품목명", "품목", "ITEM_NM"),
        "address": pick(
            "grpStdgCdNm",
            "stdgCdNm",
            "addr",
            "단체법정동코드명",
            "소재지",
            "주소",
            "STDG_CD_NM",
        ),
        "valid_period": " ~ ".join(
            p
            for p in [
                pick("certVldBgngYmd", "인증유효시작일자", "유효기간시작일"),
                pick("certVldEndYmd", "인증유효종료일자", "유효기간종료일"),
            ]
            if p
        ),
        "farm_count": pick("regFrmhsCnt", "등록농가수"),
        "cert_agency": pick("certInstNm", "인증기관명"),
        "raw_preview": json.dumps(raw, ensure_ascii=False)[:320],
    }


def _search_local_gap_csv(
    producer_name: str,
    item_name: str,
    region: str,
    cert_no: str,
    limit: int = 15,
) -> List[Dict[str, str]]:
    """data/ 폴더 내에 GAP CSV 파일(공공데이터포털 파일데이터)이 있으면 초고속 부분일치 검색을 수행한다."""
    matches: List[Dict[str, str]] = []
    csv_files: List[Path] = sorted(DATA_DIR.glob("*gap*.csv")) + sorted(DATA_DIR.glob("*GAP*.csv"))
    seen_paths = set()

    terms = [t.strip() for t in [producer_name, item_name, region, cert_no] if t and t.strip()]
    if not terms:
        return []

    for path in csv_files:
        if path in seen_paths or not path.is_file():
            continue
        seen_paths.add(path)
        try:
            raw_bytes = path.read_bytes()
            text = decode_bytes(raw_bytes)
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                joined = " ".join(str(v or "") for v in row.values())
                if all(term in joined for term in terms):
                    matches.append(_format_gap_item({str(k): str(v or "") for k, v in row.items()}))
                    if len(matches) >= limit:
                        return matches
        except Exception:
            continue
    return matches


def search_gap(
    producer_name: str = "",
    item_name: str = "",
    region: str = "",
    cert_no: str = "",
    settings: Settings | None = None,
) -> Dict[str, Any]:
    """GAP 인증정보(OpenAPI 및 로컬 CSV)에서 작목반명·대표자명·품목·법정리 소재지를 조회한다."""
    cfg = settings or Settings.load()
    local_hits = _search_local_gap_csv(producer_name, item_name, region, cert_no)

    if not cfg.has_gap:
        return {
            "ok": bool(local_hits),
            "local_csv_matches": local_hits,
            "api_notice": "DATA_GO_KR_API_KEY가 아직 설정되지 않았습니다 (.env에 키 입력 시 실시간 GAP API 조회가 활성화됩니다).",
            "portal_search_url": "https://www.gap.go.kr/portal/infosearch/gapFarmInfoSearch.do",
        }

    encoded_key = _normalize_service_key(cfg.data_go_kr_api_key)
    # 공공데이터포털 국립농산물품질관리원 GAPCERTINFO 파라미터 호환 구성
    query_params: Dict[str, Any] = {
        "pageNo": 1,
        "numOfRows": 30,
        "returnType": "json",
        "_type": "json",
    }
    if cert_no.strip():
        query_params["certNo"] = cert_no.strip()
    if producer_name.strip():
        # 생산자그룹명(작목반/농장) 또는 대표자명 파라미터 전달
        query_params["prdcrGrpNm"] = producer_name.strip()
    if item_name.strip():
        query_params["itemNm"] = item_name.strip()

    qs = urllib.parse.urlencode(query_params)
    sep = "&" if "?" in cfg.gap_api_url else "?"
    full_url = f"{cfg.gap_api_url}{sep}serviceKey={encoded_key}&{qs}"

    try:
        status, body, _ = http_get_text(full_url, timeout=14.0)
    except Exception as exc:
        return {
            "ok": bool(local_hits),
            "local_csv_matches": local_hits,
            "error": f"GAP OpenAPI 요청 실패: {exc}",
        }

    raw_items = _parse_gap_response(body)

    # 만약 prdcrGrpNm으로 결과가 없고 producer_name이 대표자 성명일 가능성이 있으면 rprsntvNm으로 2차 시도
    if not raw_items and producer_name.strip() and len(producer_name.strip()) <= 5:
        retry_params = dict(query_params)
        retry_params.pop("prdcrGrpNm", None)
        retry_params["rprsntvNm"] = producer_name.strip()
        retry_url = f"{cfg.gap_api_url}{sep}serviceKey={encoded_key}&{urllib.parse.urlencode(retry_params)}"
        try:
            status2, body2, _ = http_get_text(retry_url, timeout=14.0)
            if status2 == 200:
                raw_items = _parse_gap_response(body2)
        except Exception:
            pass

    formatted = [_format_gap_item(item) for item in raw_items]

    # region 필터가 주어졌다면 결과 내에서 지역명 일치 항목을 상단 우선 배치
    if region.strip() and formatted:
        reg_tokens = [t for t in region.strip().split() if t]
        formatted.sort(
            key=lambda x: sum(1 for tok in reg_tokens if tok in x.get("address", "") or tok in x.get("raw_preview", "")),
            reverse=True,
        )

    return {
        "ok": status == 200,
        "status": status,
        "api_matches": formatted[:15],
        "local_csv_matches": local_hits,
        "source_url": "https://www.gap.go.kr/portal/infosearch/gapFarmInfoSearch.do",
        "raw_hint": body[:240] if (status != 200 or not formatted) else "",
    }

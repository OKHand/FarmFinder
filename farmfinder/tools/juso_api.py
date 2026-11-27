"""행정안전부 도로명주소 검색 Open API 검증 도구 (business.juso.go.kr)."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List

from ..config import Settings
from .http_client import http_get_text

JUSO_API_ENDPOINT = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
RESERVED_SQL_WORDS = (
    "OR",
    "SELECT",
    "INSERT",
    "DELETE",
    "UPDATE",
    "CREATE",
    "DROP",
    "EXEC",
    "UNION",
    "FETCH",
    "DECLARE",
    "TRUNCATE",
)


def _sanitize_juso_keyword(keyword: str) -> str:
    """도로명주소 API 보안 규정에 따라 특수문자 및 예약어를 제거한다."""
    cleaned = re.sub(r"[%=><\"';]", " ", keyword)
    for word in RESERVED_SQL_WORDS:
        cleaned = re.sub(rf"\b{word}\b", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _query_juso_once(keyword: str, api_key: str, count: int = 10) -> Dict[str, Any]:
    params = {
        "confmKey": api_key,
        "currentPage": 1,
        "countPerPage": count,
        "keyword": keyword,
        "resultType": "json",
    }
    url = f"{JUSO_API_ENDPOINT}?{urllib.parse.urlencode(params)}"
    status, body, _ = http_get_text(url, timeout=10.0)
    if status != 200:
        return {"ok": False, "error": f"HTTP {status}: {body[:160]}"}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"응답 JSON 파싱 실패: {body[:160]}"}

    results = parsed.get("results", {})
    common = results.get("common", {})
    err_code = common.get("errorCode", "0")
    if err_code != "0":
        return {
            "ok": False,
            "error_code": err_code,
            "error": common.get("errorMessage", "도로명주소 API 오류"),
        }

    juso_list = results.get("juso") or []
    normalized: List[Dict[str, str]] = []
    for item in juso_list:
        normalized.append(
            {
                "road_address": item.get("roadAddr", ""),
                "jibun_address": item.get("jibunAddr", ""),
                "postal_code": item.get("zipNo", ""),
                "building_name": item.get("bdNm", ""),
                "sido": item.get("siNm", ""),
                "sigungu": item.get("sggNm", ""),
                "eup_myeon_dong": item.get("emdNm", ""),
                "legal_ri": item.get("liNm", ""),
            }
        )
    return {
        "ok": True,
        "total_count": int(common.get("totalCount", "0") or 0),
        "addresses": normalized,
    }


def verify_road_address(keyword: str, settings: Settings | None = None) -> Dict[str, Any]:
    """추정 주소(시·군·읍·면·리·번지 또는 도로명·건물명)를 행안부 도로명주소 API로 검증·정규화한다."""
    cfg = settings or Settings.load()
    sanitized = _sanitize_juso_keyword(keyword)
    if not sanitized:
        return {"ok": False, "keyword": keyword, "error": "유효한 주소 검색어가 비어 있습니다."}

    if not cfg.has_juso:
        return {
            "ok": False,
            "keyword": sanitized,
            "error": "JUSO_API_KEY가 아직 설정되지 않았습니다 (.env에 행안부 승인키를 입력해 주세요).",
        }

    try:
        primary = _query_juso_once(sanitized, cfg.juso_api_key)
    except Exception as exc:
        return {"ok": False, "keyword": sanitized, "error": f"도로명주소 API 호출 실패: {exc}"}

    # 상세 호수/농원명 등이 섞여 0건일 경우 마지막 어절을 줄여가며 1회 자동 재시도
    if primary.get("ok") and not primary.get("addresses"):
        tokens = sanitized.split()
        if len(tokens) >= 3:
            shorter = " ".join(tokens[:-1])
            try:
                fallback = _query_juso_once(shorter, cfg.juso_api_key)
                if fallback.get("ok") and fallback.get("addresses"):
                    fallback["queried_keyword"] = shorter
                    fallback["note"] = f"원검색어('{sanitized}') 결과 없음 -> '{shorter}'로 확장 검증"
                    return fallback
            except Exception:
                pass

    primary["queried_keyword"] = sanitized
    return primary

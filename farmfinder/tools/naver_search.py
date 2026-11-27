"""네이버 검색 Open API 도구 (뉴스 · 블로그 · 웹문서 · 지역업체 상세주소/전화번호 조회)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Dict, List

from ..config import Settings
from .http_client import http_get_text, strip_html_tags

NAVER_ENDPOINTS = {
    "news": "https://openapi.naver.com/v1/search/news.json",
    "blog": "https://openapi.naver.com/v1/search/blog.json",
    "webkr": "https://openapi.naver.com/v1/search/webkr.json",
    "local": "https://openapi.naver.com/v1/search/local.json",
}


def _fetch_naver_category(
    category: str,
    query: str,
    display: int,
    settings: Settings,
) -> List[Dict[str, Any]]:
    base_url = NAVER_ENDPOINTS[category]
    max_display = 5 if category == "local" else min(max(display, 1), 15)
    url = f"{base_url}?{urllib.parse.urlencode({'query': query, 'display': max_display, 'sort': 'sim'})}"
    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    status, body, _ = http_get_text(url, headers=headers, timeout=10.0)
    if status != 200:
        return [{"error": f"네이버 {category} API 응답 오류 (HTTP {status}): {body[:160]}"}]

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return [{"error": f"네이버 {category} JSON 파싱 실패"}]

    cleaned_items: List[Dict[str, Any]] = []
    for item in parsed.get("items", []):
        if category == "local":
            cleaned_items.append(
                {
                    "title": strip_html_tags(item.get("title", "")),
                    "category": item.get("category", ""),
                    "telephone": item.get("telephone", ""),
                    "jibun_address": item.get("address", ""),
                    "road_address": item.get("roadAddress", ""),
                    "link": item.get("link", ""),
                }
            )
        elif category == "blog":
            cleaned_items.append(
                {
                    "title": strip_html_tags(item.get("title", "")),
                    "snippet": strip_html_tags(item.get("description", "")),
                    "blogger_name": item.get("bloggername", ""),
                    "postdate": item.get("postdate", ""),
                    "url": item.get("link", ""),
                }
            )
        elif category == "news":
            cleaned_items.append(
                {
                    "title": strip_html_tags(item.get("title", "")),
                    "snippet": strip_html_tags(item.get("description", "")),
                    "pubDate": item.get("pubDate", ""),
                    "url": item.get("originallink") or item.get("link", ""),
                    "naver_url": item.get("link", ""),
                }
            )
        else:
            cleaned_items.append(
                {
                    "title": strip_html_tags(item.get("title", "")),
                    "snippet": strip_html_tags(item.get("description", "")),
                    "url": item.get("link", ""),
                }
            )
    return cleaned_items


def search_naver(
    query: str,
    category: str = "all",
    display: int = 7,
    settings: Settings | None = None,
) -> Dict[str, Any]:
    """네이버 뉴스·블로그·웹문서·지역(상호/농원 주소·전화번호) 검색을 수행한다."""
    cfg = settings or Settings.load()
    if not cfg.has_naver:
        return {
            "ok": False,
            "query": query,
            "error": "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 설정되지 않았습니다.",
        }

    cat = category.lower().strip()
    if cat in NAVER_ENDPOINTS:
        categories = [cat]
    else:
        # all: 지역업체(농원/법인 주소·전화번호), 뉴스(작목반/대표자), 블로그(직거래 전화번호·리 단위 주소) 동시 조회
        categories = ["local", "news", "blog", "webkr"]

    results: Dict[str, Any] = {"ok": True, "query": query, "results": {}}
    for c in categories:
        results["results"][c] = _fetch_naver_category(c, query, display, cfg)
    return results

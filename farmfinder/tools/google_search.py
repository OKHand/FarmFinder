"""구글 웹·뉴스 검색 도구 (Gemini Google Search Grounding + Serper 선택 연동 + Google News RSS 백업)."""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from ..config import Settings
from .http_client import http_get_text, http_post_json, strip_html_tags


def _search_via_gemini_grounding(query: str, focus: str, settings: Settings) -> Dict[str, Any]:
    """Gemini API의 내장 Google Search Grounding을 호출해 웹 검색 결과와 출처 URL을 수집한다."""
    if not settings.has_gemini:
        return {"ok": False, "error": "GEMINI_API_KEY 미설정"}

    model = settings.gemini_search_model or settings.gemini_model or "gemini-2.5-flash"
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={urllib.parse.quote(settings.gemini_api_key)}"
    )

    focus_instruction = {
        "news": "신문 기사(농민신문, 한국농어민신문, 지역 일간지, 지자체 보도자료 등)를 중점적으로 확인해줘.",
        "blog": "블로그, 직거래 판매글, 농장 소개글, 택배 후기 등을 중점적으로 확인해줘.",
        "address": "세부 주소(읍·면·리·번지·도로명), 농장명/영농조합법인명, 대표자명, 전화번호 단서를 최우선으로 찾아줘.",
    }.get(focus, "신문 기사, 블로그, 지자체 공고, 법인/농장 정보 전반을 확인해줘.")

    prompt = (
        f"구글 웹 검색을 사용해 다음 검색어와 관련된 한국 농가·작목반·농장 정보를 조사해줘.\n"
        f"검색어: {query}\n"
        f"중점 사항: {focus_instruction}\n"
        f"확인된 대표자명(회장/반장/농장주), 작목반명, 농장/법인명, 지역명(시·군·읍·면·리·도로명·번지), "
        f"전화번호를 추측 없이 검색 결과에 나타난 그대로 구체적으로 정리해줘."
    )

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }

    status, data, raw_text = http_post_json(endpoint, payload, timeout=25.0)
    if status != 200 or not data.get("candidates"):
        err_msg = data.get("error", {}).get("message", raw_text[:240])
        return {"ok": False, "status": status, "error": err_msg}

    candidate = data["candidates"][0]
    parts = candidate.get("content", {}).get("parts", [])
    synthesized = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()

    grounding = candidate.get("groundingMetadata", {}) or {}
    executed_queries = grounding.get("webSearchQueries", []) or []
    sources: List[Dict[str, str]] = []
    for chunk in grounding.get("groundingChunks", []) or []:
        web = chunk.get("web") or {}
        uri = web.get("uri", "")
        title = web.get("title", "")
        if uri and not any(s["url"] == uri for s in sources):
            sources.append({"title": title or "Google 검색 출처", "url": uri})

    return {
        "ok": True,
        "engine": f"Gemini Google Search Grounding ({model})",
        "executed_queries": executed_queries,
        "summary": synthesized,
        "sources": sources[:10],
    }


def _search_via_serper(query: str, settings: Settings) -> Dict[str, Any]:
    """선택적으로 SERPER_API_KEY가 있을 때 구글 자연검색(KR/ko) 상위 결과를 직접 가져온다."""
    if not settings.serper_api_key:
        return {"ok": False, "error": "SERPER_API_KEY 미설정"}

    status, data, raw_text = http_post_json(
        "https://google.serper.dev/search",
        {"q": query, "gl": "kr", "hl": "ko", "num": 8},
        headers={"X-API-KEY": settings.serper_api_key},
        timeout=15.0,
    )
    if status != 200:
        return {"ok": False, "status": status, "error": raw_text[:200]}

    items = []
    for row in (data.get("organic") or [])[:8]:
        items.append(
            {
                "title": row.get("title", ""),
                "url": row.get("link", ""),
                "snippet": row.get("snippet", ""),
                "date": row.get("date", ""),
            }
        )
    return {"ok": True, "engine": "Serper Google Search", "items": items}


def _search_google_news_rss(query: str, limit: int = 8) -> List[Dict[str, str]]:
    """API 키나 과금 없이 구글 뉴스 한국판(RSS)에서 지역 일간지·농업 전문지 기사를 검색한다."""
    rss_url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    )
    try:
        status, xml_text, _ = http_get_text(rss_url, timeout=12.0)
        if status != 200 or not xml_text:
            return []
        root = ET.fromstring(xml_text)
        articles: List[Dict[str, str]] = []
        for item in root.findall(".//item")[:limit]:
            title = strip_html_tags(item.findtext("title", ""))
            link = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            desc = strip_html_tags(item.findtext("description", ""))
            source_el = item.find("source")
            press = source_el.text.strip() if (source_el is not None and source_el.text) else ""
            articles.append(
                {
                    "title": title,
                    "press": press,
                    "published_at": pub_date,
                    "snippet": desc,
                    "url": link,
                }
            )
        return articles
    except Exception:
        return []


def search_google(query: str, focus: str = "general", settings: Settings | None = None) -> Dict[str, Any]:
    """구글 웹 검색 및 구글 뉴스 검색을 수행하여 기사·블로그·웹페이지 단서를 반환한다."""
    cfg = settings or Settings.load()
    result: Dict[str, Any] = {"query": query, "focus": focus}

    # 1. Serper 직접 검색 (설정된 경우)
    if cfg.serper_api_key:
        serper_res = _search_via_serper(query, cfg)
        if serper_res.get("ok"):
            result["google_web_results"] = serper_res["items"]

    # 2. Gemini Google Search Grounding 호출
    grounding_res = _search_via_gemini_grounding(query, focus, cfg)
    if grounding_res.get("ok"):
        result["grounded_summary"] = grounding_res["summary"]
        result["grounding_sources"] = grounding_res["sources"]
        result["executed_queries"] = grounding_res["executed_queries"]
    else:
        result["grounding_notice"] = (
            f"Gemini Search Grounding 미사용/제한 ({grounding_res.get('error', '')}). "
            "Google 뉴스 RSS 결과와 네이버 검색을 함께 활용합니다."
        )

    # 3. 구글 뉴스 한국판 RSS (신문 기사 보강용으로 항상 병행)
    news_items = _search_google_news_rss(query, limit=6)
    if news_items:
        result["google_news_articles"] = news_items

    return result

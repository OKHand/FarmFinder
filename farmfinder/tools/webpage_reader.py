"""신문 기사 및 네이버 블로그 원문 열람기 (네이버 블로그 iframe 자동 우회 + 주소/전화번호 스캔)."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict

from .http_client import extract_korean_clues_from_text, html_to_clean_text, http_get_text


def _normalize_naver_blog_url(url: str) -> str:
    """데스크톱 네이버 블로그 URL은 본문이 iframe 안에 숨겨져 있으므로 모바일 URL로 변환한다."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()

    if host == "blog.naver.com":
        # 패턴 1: https://blog.naver.com/{blogId}/{logNo}
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) >= 2 and path_parts[1].isdigit():
            return f"https://m.blog.naver.com/{path_parts[0]}/{path_parts[1]}"

        # 패턴 2: PostView.naver?blogId=...&logNo=...
        qs = urllib.parse.parse_qs(parsed.query)
        blog_id = (qs.get("blogId") or [""])[0]
        log_no = (qs.get("logNo") or [""])[0]
        if blog_id and log_no:
            return f"https://m.blog.naver.com/{blog_id}/{log_no}"

    return url


def read_webpage(url: str, keyword_hint: str = "") -> Dict[str, Any]:
    """웹페이지(블로그 포스트, 뉴스 기사 등) 본문을 읽고 전화번호·세부주소 단서를 정밀 추출한다."""
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "url": url, "error": "유효하지 않은 URL입니다."}

    fetch_url = _normalize_naver_blog_url(url)
    try:
        status, raw_html, final_url = http_get_text(fetch_url, timeout=14.0)
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"페이지 접속 실패: {exc}"}

    if status >= 400 or not raw_html:
        return {"ok": False, "url": url, "status": status, "error": f"HTTP {status} 응답"}

    # 만약 여전히 iframe(mainFrame) 구조가 남아있다면 내부 src 재추적
    if "mainFrame" in raw_html and len(raw_html) < 8000:
        iframe_match = re.search(r'<iframe[^>]+id=["\']mainFrame["\'][^>]+src=["\']([^"\']+)["\']', raw_html, re.I)
        if iframe_match:
            inner_url = urllib.parse.urljoin(final_url, iframe_match.group(1))
            try:
                status2, raw_html2, final_url2 = http_get_text(inner_url, timeout=14.0)
                if status2 < 400 and raw_html2:
                    raw_html = raw_html2
                    final_url = final_url2
            except Exception:
                pass

    title, clean_body = html_to_clean_text(raw_html)
    scanned_clues = extract_korean_clues_from_text(clean_body)

    # keyword_hint가 있으면 해당 키워드 주변 문맥을 우선 발췌
    focused_excerpts = []
    if keyword_hint.strip():
        tokens = [t for t in re.split(r"\s+", keyword_hint.strip()) if len(t) >= 2]
        for token in tokens[:4]:
            for m in re.finditer(re.escape(token), clean_body, re.IGNORECASE):
                start = max(0, m.start() - 160)
                end = min(len(clean_body), m.end() + 220)
                snippet = clean_body[start:end].replace("\n", " ").strip()
                if snippet and snippet not in focused_excerpts:
                    focused_excerpts.append(snippet)
                if len(focused_excerpts) >= 6:
                    break

    return {
        "ok": True,
        "original_url": url,
        "fetched_url": final_url,
        "title": title,
        "detected_phones": scanned_clues["phones"],
        "detected_address_candidates": scanned_clues["address_candidates"],
        "keyword_excerpts": focused_excerpts,
        "content_excerpt": clean_body[:4500],
    }

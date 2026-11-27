"""Gemini Function Calling 기반 2단계 농가·작목반 탐색 에이전트.

- 1단계 (Regional Discovery): #지역 + #품목 초기 조건으로 지역 대표 작목반·공선회·선도농가·영농법인을 발굴
- 2단계 (Deep Address Investigation): 선택된 작목반·농가의 단서를 꼬리물기로 좁혀 세부주소(~리·번지·도로명)와 전화번호를 특정
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any, Dict, Iterator, List, Tuple

from .config import Settings
from .models import (
    ClueInput,
    DiscoveredClue,
    DiscoveryInput,
    DiscoveryResult,
    FarmCandidate,
    InvestigationResult,
    RegionalLeaderItem,
    SourceReference,
    TraceStep,
)
from .storage import save_investigation
from .tools import (
    read_webpage,
    search_atfis,
    search_gap,
    search_google,
    search_naver,
    verify_road_address,
)
from .tools.http_client import http_post_json

TOOL_LABELS = {
    "search_google": "구글 웹·뉴스 검색",
    "search_naver": "네이버 뉴스·블로그·지역 검색",
    "read_webpage": "기사·블로그 원문 정밀 열람",
    "search_gap": "GAP 인증농가 OpenAPI 조회",
    "search_atfis": "aT FIS 생산자정보 조회",
    "verify_road_address": "행안부 도로명주소 검증",
}

GEMINI_TOOL_DECLARATIONS = [
    {
        "name": "search_google",
        "description": (
            "구글 웹 검색 및 신문 기사·블로그·지자체 공고를 검색합니다. "
            "지역 대표 작목반·선도농가를 발굴하거나, 새로 발견한 농장명/대표자명으로 재검색할 때 사용하세요."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "구글 검색어 (예: '의성 사과 작목반 회장', '봉양면 해오름농원 주소')",
                },
                "focus": {
                    "type": "STRING",
                    "description": "검색 초점: general | news | blog | address",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_naver",
        "description": (
            "네이버 오픈 API로 뉴스(news), 블로그(blog), 지역업체(local - 농원/법인 주소·전화번호), 웹문서(webkr), "
            "또는 전체(all)를 검색합니다."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "네이버 검색어 (예: '의성 사과 공선출하회', '봉양면 해오름농원 사과')",
                },
                "category": {
                    "type": "STRING",
                    "description": "검색 대상: all | news | blog | local | webkr",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_webpage",
        "description": (
            "검색 결과에서 발견한 유망한 네이버 블로그 포스트나 신문 기사 URL의 본문 전체를 읽고 "
            "요약문에 잘려 있는 대표자 성명, 작목반 현황, 세부 주소(~리, 번지, 도로명), 전화번호를 정밀 추출합니다."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "STRING", "description": "열람할 기사 또는 블로그 URL"},
                "keyword_hint": {
                    "type": "STRING",
                    "description": "본문 내 중점적으로 찾을 단어 (예: 작목반, 회장, 농장명, 리 이름)",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "search_gap",
        "description": (
            "국립농산물품질관리원 GAP 인증정보 OpenAPI 및 로컬 CSV에서 작목반/생산자그룹명, 대표자명, 품목명, "
            "단체법정동코드명(시·군·읍·면·리), 등록농가수를 조회합니다."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "producer_name": {
                    "type": "STRING",
                    "description": "생산자그룹명(작목반/법인/농장명) 또는 대표자 성명 (모를 경우 비워두고 품목·지역으로 조회)",
                },
                "item_name": {"type": "STRING", "description": "품목명 (예: 사과, 딸기, 배)"},
                "region": {"type": "STRING", "description": "지역 단서 (예: 의성군, 논산시)"},
                "cert_no": {"type": "STRING", "description": "GAP 인증번호 (알고 있는 경우)"},
            },
        },
    },
    {
        "name": "search_atfis",
        "description": (
            "한국농수산식품유통공사 aT FIS 생산자정보(영농조합법인, 농업회사법인, 농협, 친환경단체 등)에서 "
            "농가/법인명, 품목, 소재지, 전화번호, 대표자명을 조회합니다."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "keyword": {"type": "STRING", "description": "농가명, 법인명, 작목반명 검색어 (모를 경우 비워둠)"},
                "item_name": {"type": "STRING", "description": "생산 품목명"},
                "region": {"type": "STRING", "description": "지역명 (예: 경상북도 의성군, 전라남도 해남군)"},
            },
        },
    },
    {
        "name": "verify_road_address",
        "description": (
            "행정안전부 도로명주소 Open API를 호출해 후보 주소(읍·면·리·번지 또는 도로명·건물명)가 실제 존재하는지 검증하고 "
            "정확한 도로명주소, 지번주소(법정리·번지), 우편번호를 확정합니다."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "keyword": {
                    "type": "STRING",
                    "description": "검증할 주소 키워드 (예: '의성군 봉양면 도원리 412', '전남 영암군 신북면 장상리 684')",
                },
            },
            "required": ["keyword"],
        },
    },
]

DISCOVERY_SYSTEM_INSTRUCTION = """당신은 대한민국 농산물 주산지 분석 및 대표 생산자조직 발굴 에이전트 'FarmFinder'입니다.

사용자가 초기 조건으로 [지역]과 [품목]만 입력했습니다. 아직 구체적인 세부 번지까지 들어가기 전에, 해당 지역·품목을 대표하는:
1) 대표 작목반 / 공선출하회 / 품목연구회 (회장·반장·총무 성명 포함)
2) 지역 대표·선도 농가 (농업명인, 수상 농가, 수출 선도농가, 유명 직거래 농원 등)
3) 주요 영농조합법인 / 농업회사법인 / 조공법인
을 폭넓게 조사하여 한눈에 비교·선택할 수 있는 목록으로 정리해 주세요.

[1단계 발굴 지침]
1. `search_google` (예: '{지역} {품목} 작목반 회장', '{지역} {품목} 공선출하회 선도농가'), `search_naver` (뉴스·블로그·지역업체), `search_gap` (품목·지역 기준 단체인증/농가 조회), `search_atfis` (품목·지역 기준 법인/농협 조회)를 고루 활용하세요.
2. 유망한 신문 기사(농민신문, 한국농어민신문, 지방지 등)가 보이면 필요시 `read_webpage`로 본문을 열어 정확한 작목반명, 회장/대표 성명, 소속 읍·면을 확인하세요.
3. 절대 실존하지 않는 단체명이나 인명을 지어내지 마세요. 실제 검색 결과·공공데이터에서 확인된 단체·농가만 포함하세요.
4. 확인된 권역 주소(`sub_region`)는 1단계에서 파악된 수준(예: '경북 의성군 봉양면', '충남 논산시 상월면' 등)으로 적고, 이미 전화번호나 상세 주소가 같이 나온 경우에만 함께 기재하세요.

최종 응답은 반드시 아래 순수 JSON 스키마 형식으로만 출력하세요:
{
  "overview_summary": "해당 지역 내 이 품목의 주요 주산지 읍·면 분포 및 작목반·출하조직 현황을 2~3문장으로 요약",
  "leaders": [
    {
      "leader_type": "대표 작목반 | 공선출하회·연구회 | 대표·선도 농가 | 영농조합·법인",
      "crop_group_name": "작목반/공선회/연구회 명칭 (개별 농가·법인이면 소속 단체가 있을 때만 기재, 없으면 빈 문자열)",
      "farm_name": "농장명 또는 영농조합법인명 (작목반 자체일 경우 빈 문자열 가능)",
      "representative": "확인된 회장/반장/대표자/농가주 성명 (미확인 시 빈 문자열)",
      "crop_item": "품목명",
      "sub_region": "확인된 시·군·읍·면 단위 소재지",
      "scale_or_badge": "핵심 특징 태그 (예: 'GAP 단체인증 · 38농가', '수출 선도조직', '농민신문 보도 선도농가')",
      "summary": "어떤 기사·공공DB에서 확인되었으며 왜 대표적인지 1~2문장 설명",
      "phone": "1단계에서 바로 확인된 전화번호가 있으면 기재, 없으면 빈 문자열",
      "source_title": "출처 기사/DB 제목",
      "source_url": "https://...",
      "source_type": "구글검색 | 네이버뉴스 | 네이버블로그 | 네이버지역 | GAP인증 | aT_FIS"
    }
  ]
}
"""

INVESTIGATION_SYSTEM_INSTRUCTION = """당신은 대한민국 농산물 산지·작목반·농가 정보를 끝까지 추적해 교차 검증하는 전문 조사 에이전트 'FarmFinder'입니다.

[핵심 탐색 원칙: 2단계 꼬리물기(단서 확장 및 세부주소·연락처 특정)]
1. 1차 검색만으로 '~군 ~면' 수준에서 멈추지 마세요.
2. 검색 도중 새로운 단서 — ① 읍·면 이름, ② 법정리 이름, ③ 농장명/과수원명/영농조합법인명, ④ 작목반 회장·반장·총무·대표자 성명, ⑤ 인증번호 — 가 하나라도 발견되면, 반드시 그 새 단서를 조합하여 2차·3차 검색(구글, 네이버 블로그/지역업체, GAP, aT FIS)을 이어가세요.
   - 예시 A: 신문 기사에서 '봉양면'과 '김OO 회장'이 나왔다면 -> '봉양면 김OO 사과', '의성 김OO 농원'으로 구글·네이버 블로그·GAP 재검색
   - 예시 B: 중간에 '해오름농원'이라는 농장명이 나왔다면 -> '봉양면 해오름농원'으로 네이버 지역/블로그 검색 및 원문 열람(read_webpage) 수행
3. 블로그나 기사 요약문에 주소 일부나 농장명이 보이면 반드시 `read_webpage`로 본문을 열어 요약문에 잘려 있는 '~리 번지', '도로명 주소', '전화번호(010-..., 지역번호)'를 직접 확인하세요.
4. 번지나 도로명 후보가 확보되면 반드시 `verify_road_address`를 호출해 실제 행정안전부 도로명주소와 지번주소를 확정하세요.
5. 절대 존재하지 않는 번지나 전화번호를 지어내지(Hallucination 금지) 마세요. 확인된 수준(예: 세부번지 확정 vs 리 단위 확인 vs 읍·면 단위 확인)을 정직하게 표기하고 모든 근거 출처 URL을 남기세요.

모든 탐색이 끝나면 최종 응답은 반드시 아래 JSON 스키마 형식으로만 출력하세요:
{
  "best_match": {
    "crop_group_name": "확인된 작목반/단체명 (없으면 빈 문자열)",
    "farm_name": "확인된 농장명/영농조합법인명",
    "representative": "확인된 대표자/회장/농가주 성명",
    "crop_item": "품목명",
    "road_address": "행안부 검증 완료된 도로명 세부주소 (미확정 시 빈 문자열)",
    "jibun_address": "지번 세부주소 또는 확인된 최대 행정구역(~시/군 ~읍/면 ~리 번지)",
    "postal_code": "우편번호",
    "phone": "확인된 전화번호 (블로그/법인DB 등에서 발견 시 기재, 없으면 빈 문자열)",
    "address_precision": "세부번지 확정 | 리 단위 확인 | 읍·면 단위 확인 | 시·군 단위 확인 | 미확인",
    "verification_status": "교차검증 완료 | 단일출처 확인 | 추가확인 필요",
    "confidence": 85,
    "reasoning_summary": "1차~N차 탐색을 통해 어떻게 단서를 좁히고 교차 확인했는지 2~4문장으로 명확히 요약",
    "sources": [
      {
        "title": "출처 제목",
        "url": "https://...",
        "source_type": "구글검색 | 네이버뉴스 | 네이버블로그 | 네이버지역 | GAP인증 | aT_FIS | 도로명주소API",
        "matched_info": "이 출처에서 얻은 단서 (예: 봉양면·대표자명 확인 / 도원리 번지·전화번호 확인)"
      }
    ]
  },
  "discovered_clues": [
    {
      "step": 1,
      "clue_type": "읍/면 | 리/번지 | 농장/법인명 | 대표자명 | 전화번호 | 인증정보",
      "value": "발견된 단서 값",
      "found_via": "구글 뉴스 / 네이버 블로그 / GAP / aT FIS 등"
    }
  ],
  "candidates": [
    {
      "crop_group_name": "기타 후보 단체명",
      "farm_name": "기타 후보 농장/법인명",
      "representative": "대표자명",
      "crop_item": "품목",
      "road_address": "도로명주소",
      "jibun_address": "지번주소",
      "postal_code": "",
      "phone": "전화번호",
      "address_precision": "리 단위 확인",
      "verification_status": "단일출처 확인",
      "confidence": 60,
      "reasoning_summary": "후보로 분류한 이유",
      "sources": []
    }
  ],
  "follow_up_suggestions": [
    "더 좁히기 위해 추가 입력하거나 재탐색해볼 수 있는 제안 문구 1~3개"
  ]
}
"""


def _execute_tool(name: str, args: Dict[str, Any], settings: Settings) -> Tuple[Dict[str, Any], str, str, List[str]]:
    """단일 도구를 실행하고 (결과 JSON, 쿼리 요약, 핵심 발견 요약, 관련 URL 리스트)를 반환한다."""
    urls: List[str] = []

    if name == "search_google":
        q = str(args.get("query", ""))
        focus = str(args.get("focus", "general"))
        res = search_google(query=q, focus=focus, settings=settings)
        for s in res.get("grounding_sources", []) or []:
            if s.get("url"):
                urls.append(s["url"])
        for n in res.get("google_news_articles", []) or []:
            if n.get("url"):
                urls.append(n["url"])
        for w in res.get("google_web_results", []) or []:
            if w.get("url"):
                urls.append(w["url"])
        summary_bits = []
        if res.get("grounded_summary"):
            summary_bits.append(f"구글 웹 요약 확보 ({len(res.get('grounding_sources', []))}개 웹 출처)")
        if res.get("google_news_articles"):
            summary_bits.append(f"구글 뉴스 기사 {len(res['google_news_articles'])}건")
        if not summary_bits:
            summary_bits.append(res.get("grounding_notice", "검색 완료"))
        return res, f"검색어: {q}", " · ".join(summary_bits), urls[:6]

    if name == "search_naver":
        q = str(args.get("query", ""))
        cat = str(args.get("category", "all"))
        res = search_naver(query=q, category=cat, settings=settings)
        if not res.get("ok"):
            return res, f"[{cat}] {q}", res.get("error", "네이버 API 미설정"), []
        counts = []
        for k, items in (res.get("results") or {}).items():
            valid_items = [i for i in items if not i.get("error")]
            counts.append(f"{k}:{len(valid_items)}건")
            for i in valid_items[:3]:
                u = i.get("url") or i.get("link")
                if u:
                    urls.append(u)
        return res, f"[{cat}] {q}", f"네이버 검색 결과 ({', '.join(counts)})", urls[:6]

    if name == "read_webpage":
        u = str(args.get("url", ""))
        hint = str(args.get("keyword_hint", ""))
        res = read_webpage(url=u, keyword_hint=hint)
        if not res.get("ok"):
            return res, u, f"열람 실패 ({res.get('error', '')})", [u] if u else []
        phones = res.get("detected_phones") or []
        addrs = res.get("detected_address_candidates") or []
        found_msg = f"본문 열람 완료 (전화번호 후보 {len(phones)}건, 주소 패턴 {len(addrs)}건 감지)"
        return res, f"{res.get('title') or u[:55]}", found_msg, [u]

    if name == "search_gap":
        p_name = str(args.get("producer_name", ""))
        item = str(args.get("item_name", ""))
        reg = str(args.get("region", ""))
        c_no = str(args.get("cert_no", ""))
        res = search_gap(
            producer_name=p_name,
            item_name=item,
            region=reg,
            cert_no=c_no,
            settings=settings,
        )
        q_desc = " / ".join(x for x in [p_name, item, reg, c_no] if x) or "전체"
        total = len(res.get("api_matches") or []) + len(res.get("local_csv_matches") or [])
        msg = f"GAP 인증 매칭 {total}건" if res.get("ok") else res.get("api_notice") or res.get("error", "0건")
        return res, q_desc, msg, ["https://www.gap.go.kr/portal/infosearch/gapFarmInfoSearch.do"]

    if name == "search_atfis":
        kw = str(args.get("keyword", ""))
        item = str(args.get("item_name", ""))
        reg = str(args.get("region", ""))
        res = search_atfis(keyword=kw, item_name=item, region=reg)
        q_desc = " / ".join(x for x in [kw, item, reg] if x) or "생산자 조회"
        web_cnt = len(res.get("web_matches") or [])
        loc_cnt = len(res.get("local_matches") or [])
        for m in (res.get("web_matches") or [])[:3]:
            if m.get("source_url"):
                urls.append(m["source_url"])
        msg = f"aT FIS 매칭 {web_cnt + loc_cnt}건" if res.get("ok") else res.get("error", "조회 실패")
        return res, q_desc, msg, urls or ["https://www.atfis.or.kr/home/producer.do"]

    if name == "verify_road_address":
        kw = str(args.get("keyword", ""))
        res = verify_road_address(keyword=kw, settings=settings)
        addrs = res.get("addresses") or []
        if res.get("ok") and addrs:
            top = addrs[0]
            msg = f"도로명 확정: {top.get('road_address', '')} ({top.get('jibun_address', '')})"
        elif res.get("ok"):
            msg = "일치하는 도로명주소 없음 (번지/리 재확인 필요)"
        else:
            msg = res.get("error", "도로명주소 API 미설정/오류")
        return res, f"주소검증: {kw}", msg, []

    return {"error": f"알 수 없는 도구: {name}"}, name, "지원되지 않는 도구", []


def _extract_json_block(text: str) -> Dict[str, Any]:
    """모델 응답에서 JSON 객체를 안전하게 추출한다."""
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            cleaned = cleaned[brace_start : brace_end + 1]
    return json.loads(cleaned)


def _run_gemini_tool_loop(
    system_instruction: str,
    user_prompt: str,
    tool_declarations: List[Dict[str, Any]],
    max_steps: int,
    settings: Settings,
    trace_steps_out: List[TraceStep],
) -> Iterator[Tuple[str, Any]]:
    """공통 Gemini Function Calling 루프 실행기."""
    model = settings.gemini_model or "gemini-2.5-flash"
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={urllib.parse.quote(settings.gemini_api_key)}"
    )

    contents: List[Dict[str, Any]] = [{"role": "user", "parts": [{"text": user_prompt}]}]
    step_counter = 0
    final_text = ""

    for round_idx in range(1, max_steps + 1):
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "tools": [{"functionDeclarations": tool_declarations}],
            "generationConfig": {"temperature": 0.15},
        }

        status, data, raw_text = http_post_json(endpoint, payload, timeout=40.0)
        if status != 200 or not data.get("candidates"):
            err_detail = data.get("error", {}).get("message", raw_text[:300])
            yield ("error", f"Gemini API 호출 실패 (HTTP {status}): {err_detail}")
            return

        candidate_content = data["candidates"][0].get("content") or {"role": "model", "parts": []}
        parts = candidate_content.get("parts") or []
        contents.append(candidate_content)

        fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not fn_calls:
            final_text = "\n".join(p.get("text", "") for p in parts if p.get("text") and not p.get("thought")).strip()
            break

        fn_response_parts: List[Dict[str, Any]] = []
        for fc in fn_calls:
            step_counter += 1
            fn_name = fc.get("name", "")
            fn_args = fc.get("args") or {}
            label = TOOL_LABELS.get(fn_name, fn_name)

            yield (
                "event",
                {
                    "event": "tool_start",
                    "step_no": step_counter,
                    "round": round_idx,
                    "tool_name": fn_name,
                    "tool_label": label,
                    "args": fn_args,
                },
            )

            tool_output, q_summary, found_summary, urls = _execute_tool(fn_name, fn_args, settings)
            step_obj = TraceStep(
                step_no=step_counter,
                tool_name=fn_name,
                tool_label=label,
                query_summary=q_summary,
                findings_summary=found_summary,
                urls=urls,
            )
            trace_steps_out.append(step_obj)

            yield ("event", {"event": "tool_end", "step": step_obj.model_dump()})

            resp_obj: Dict[str, Any] = {"name": fn_name, "response": {"result": tool_output}}
            if fc.get("id"):
                resp_obj["id"] = fc["id"]
            fn_response_parts.append({"functionResponse": resp_obj})

        contents.append({"role": "user", "parts": fn_response_parts})

    parsed_json: Dict[str, Any] = {}
    if final_text:
        try:
            parsed_json = _extract_json_block(final_text)
        except Exception:
            parsed_json = {}

    if not parsed_json:
        yield (
            "event",
            {
                "event": "status",
                "message": "수집된 검색·공공DB 결과를 교차 정리하여 목록을 생성하고 있습니다...",
            },
        )
        contents.append(
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "지금까지 도구 호출로 확인한 모든 정보와 출처 URL을 종합하여 "
                            "시스템 지침에 정의된 순수 JSON 형식으로 최종 결과를 출력해 주세요."
                        )
                    }
                ],
            }
        )
        finalize_payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        status, data, _ = http_post_json(endpoint, finalize_payload, timeout=35.0)
        if status == 200 and data.get("candidates"):
            parts = data["candidates"][0].get("content", {}).get("parts", [])
            txt = "\n".join(p.get("text", "") for p in parts if p.get("text") and not p.get("thought")).strip()
            try:
                parsed_json = _extract_json_block(txt)
            except Exception:
                parsed_json = {}

    yield ("parsed_json", (parsed_json, final_text))


# ============================================================================
# [1단계] 지역 + 품목 기반 대표 작목반 · 대표 농가 발굴 (Regional Discovery)
# ============================================================================

def run_live_discovery(req: DiscoveryInput, settings: Settings) -> Iterator[Dict[str, Any]]:
    prompt_lines = [
        f"초기 조건 — 지역: '{req.region.strip()}', 품목: '{req.crop_item.strip()}'",
    ]
    if req.focus_hint.strip():
        prompt_lines.append(f"추가 관심 조건: {req.focus_hint.strip()}")
    prompt_lines.extend(
        [
            "",
            "위 지역의 해당 품목에 대해 대표적인 작목반/공선출하회/연구회, 대표·선도 농가(회장·명인·선도농가), 주요 영농조합법인을 발굴해 주세요.",
            "구글 웹·뉴스(search_google), 네이버 뉴스·블로그·지역(search_naver), GAP 인증정보(search_gap), aT FIS 생산자정보(search_atfis)를 조합해 실제 확인된 단체·농가 4~8곳을 추려주세요.",
        ]
    )

    # 1단계에서는 도로명 번지 검증(verify_road_address)을 제외한 5개 검색·열람 도구만 제공해 발굴 속도를 높임
    discovery_tools = [t for t in GEMINI_TOOL_DECLARATIONS if t["name"] != "verify_road_address"]
    trace_steps: List[TraceStep] = []

    yield {
        "event": "status",
        "message": f"'{req.region} · {req.crop_item}' 대표 작목반 및 선도 농가를 발굴하고 있습니다...",
    }

    parsed_json: Dict[str, Any] = {}
    fallback_text = ""

    for kind, payload in _run_gemini_tool_loop(
        system_instruction=DISCOVERY_SYSTEM_INSTRUCTION,
        user_prompt="\n".join(prompt_lines),
        tool_declarations=discovery_tools,
        max_steps=req.max_steps,
        settings=settings,
        trace_steps_out=trace_steps,
    ):
        if kind == "error":
            yield {"event": "error", "message": payload}
            return
        elif kind == "event":
            yield payload
        elif kind == "parsed_json":
            parsed_json, fallback_text = payload

    leaders: List[RegionalLeaderItem] = []
    for raw_item in parsed_json.get("leaders") or []:
        if not isinstance(raw_item, dict):
            continue
        leaders.append(
            RegionalLeaderItem(
                leader_type=str(raw_item.get("leader_type") or "대표 작목반"),
                crop_group_name=str(raw_item.get("crop_group_name") or ""),
                farm_name=str(raw_item.get("farm_name") or ""),
                representative=str(raw_item.get("representative") or ""),
                crop_item=str(raw_item.get("crop_item") or req.crop_item),
                sub_region=str(raw_item.get("sub_region") or req.region),
                scale_or_badge=str(raw_item.get("scale_or_badge") or ""),
                summary=str(raw_item.get("summary") or ""),
                phone=str(raw_item.get("phone") or ""),
                source_title=str(raw_item.get("source_title") or "출처 확인"),
                source_url=str(raw_item.get("source_url") or ""),
                source_type=str(raw_item.get("source_type") or "구글검색"),
            )
        )

    result = DiscoveryResult(
        mode_used="live",
        region=req.region.strip(),
        crop_item=req.crop_item.strip(),
        overview_summary=str(
            parsed_json.get("overview_summary")
            or fallback_text[:300]
            or f"{req.region} 지역의 {req.crop_item} 주요 작목반 및 생산자 탐색 결과입니다."
        ),
        leaders=leaders,
        trace_steps=trace_steps,
    )
    yield {"event": "discovery_complete", "result": result.model_dump()}


def stream_regional_discovery(req: DiscoveryInput, settings: Settings | None = None) -> Iterator[Dict[str, Any]]:
    cfg = settings or Settings.load()
    if not cfg.has_gemini:
        yield {
            "event": "error",
            "message": "1단계 대표 발굴을 실행하려면 GEMINI_API_KEY가 필요합니다. 우측 상단 [API · 시트 설정] 또는 .env 파일에 발급받은 키를 입력해 주세요.",
        }
        return
    yield from run_live_discovery(req, cfg)


# ============================================================================
# [2단계] 선택 대상(또는 직접 입력 단서) 세부주소 · 연락처 꼬리물기 추적
# ============================================================================

def _build_initial_user_prompt(clue: ClueInput) -> str:
    lines = ["다음 단서를 바탕으로 해당 작목반 대표자 또는 농가의 세부 주소(~리·번지·도로명)와 전화번호를 끝까지 추적해 주세요.", ""]
    if clue.region.strip():
        lines.append(f"- 알고 있는 지역(주소 일부): {clue.region.strip()}")
    if clue.crop_item.strip():
        lines.append(f"- 품목명: {clue.crop_item.strip()}")
    if clue.crop_group.strip():
        lines.append(f"- 작목반/단체명: {clue.crop_group.strip()}")
    if clue.farm_or_person.strip():
        lines.append(f"- 농가명/농장명/법인명 단서: {clue.farm_or_person.strip()}")
    if clue.representative.strip():
        lines.append(f"- 파악된 대표자/회장 성명: {clue.representative.strip()}")
    if clue.extra_clue.strip():
        lines.append(f"- 추가 힌트/요청사항: {clue.extra_clue.strip()}")
    if clue.prior_clues:
        lines.append(f"- 이전 탐색에서 확보된 단서들: {', '.join(clue.prior_clues)}")

    lines.extend(
        [
            "",
            "탐색 지침:",
            "1. 구글 웹 검색(search_google)과 네이버 검색(search_naver), GAP/aT FIS 조회를 활용해 읍·면, 법정리, 농장명, 대표자명을 교차 확인하세요.",
            "2. 읍·면이나 농장명, 대표자명이 나오면 반드시 그 단서로 재검색해 '~리'와 세부 번지, 전화번호까지 좁히세요.",
            "3. 유망한 블로그 글이나 신문 기사는 read_webpage로 원문을 열어 전화번호와 상세주소를 확인하세요.",
            "4. 주소 후보가 나오면 verify_road_address로 정확한 도로명·지번 주소를 검증한 뒤 지정된 JSON 형식으로 정리하세요.",
        ]
    )
    return "\n".join(lines)


def run_live_investigation(clue: ClueInput, settings: Settings) -> Iterator[Dict[str, Any]]:
    """실제 Gemini Function Calling 루프를 돌며 단계별 진행 상황을 실시간 스트리밍한다."""
    model = settings.gemini_model or "gemini-2.5-flash"
    trace_steps: List[TraceStep] = []

    yield {
        "event": "status",
        "message": f"Gemini ({model}) 2단계 세부주소 꼬리물기 추적을 시작합니다...",
    }

    parsed_json: Dict[str, Any] = {}
    final_text = ""

    for kind, payload in _run_gemini_tool_loop(
        system_instruction=INVESTIGATION_SYSTEM_INSTRUCTION,
        user_prompt=_build_initial_user_prompt(clue),
        tool_declarations=GEMINI_TOOL_DECLARATIONS,
        max_steps=clue.max_steps,
        settings=settings,
        trace_steps_out=trace_steps,
    ):
        if kind == "error":
            yield {"event": "error", "message": payload}
            return
        elif kind == "event":
            yield payload
        elif kind == "parsed_json":
            parsed_json, final_text = payload

    best_raw = parsed_json.get("best_match") or {}
    best_candidate = FarmCandidate(
        crop_group_name=str(best_raw.get("crop_group_name") or clue.crop_group or ""),
        farm_name=str(best_raw.get("farm_name") or clue.farm_or_person or ""),
        representative=str(best_raw.get("representative") or clue.representative or ""),
        crop_item=str(best_raw.get("crop_item") or clue.crop_item or ""),
        road_address=str(best_raw.get("road_address") or ""),
        jibun_address=str(best_raw.get("jibun_address") or ""),
        postal_code=str(best_raw.get("postal_code") or ""),
        phone=str(best_raw.get("phone") or ""),
        address_precision=str(best_raw.get("address_precision") or "미확인"),
        verification_status=str(best_raw.get("verification_status") or "추가확인 필요"),
        confidence=int(best_raw.get("confidence") or 50),
        reasoning_summary=str(best_raw.get("reasoning_summary") or final_text[:400] or "탐색 완료"),
        sources=[
            SourceReference(
                title=str(s.get("title") or "출처 링크"),
                url=str(s.get("url") or ""),
                source_type=str(s.get("source_type") or "웹문서"),
                matched_info=str(s.get("matched_info") or ""),
            )
            for s in (best_raw.get("sources") or [])
            if isinstance(s, dict)
        ],
    )

    other_candidates = [
        FarmCandidate(
            crop_group_name=str(c.get("crop_group_name") or ""),
            farm_name=str(c.get("farm_name") or ""),
            representative=str(c.get("representative") or ""),
            crop_item=str(c.get("crop_item") or ""),
            road_address=str(c.get("road_address") or ""),
            jibun_address=str(c.get("jibun_address") or ""),
            postal_code=str(c.get("postal_code") or ""),
            phone=str(c.get("phone") or ""),
            address_precision=str(c.get("address_precision") or "미확인"),
            verification_status=str(c.get("verification_status") or "단일출처 확인"),
            confidence=int(c.get("confidence") or 50),
            reasoning_summary=str(c.get("reasoning_summary") or ""),
        )
        for c in (parsed_json.get("candidates") or [])
        if isinstance(c, dict)
    ]

    discovered_clues = [
        DiscoveredClue(
            step=int(dc.get("step") or idx + 1),
            clue_type=str(dc.get("clue_type") or "단서"),
            value=str(dc.get("value") or ""),
            found_via=str(dc.get("found_via") or ""),
        )
        for idx, dc in enumerate(parsed_json.get("discovered_clues") or [])
        if isinstance(dc, dict) and dc.get("value")
    ]

    result = InvestigationResult(
        mode_used="live",
        input_summary=clue.summary_text(),
        best_match=best_candidate,
        candidates=other_candidates,
        discovered_clues=discovered_clues,
        trace_steps=trace_steps,
        follow_up_suggestions=[str(x) for x in (parsed_json.get("follow_up_suggestions") or [])[:4]],
    )
    result.record_id = save_investigation(result)
    yield {"event": "complete", "result": result.model_dump()}


def stream_investigation(clue: ClueInput, settings: Settings | None = None) -> Iterator[Dict[str, Any]]:
    """2단계 실시간 세부주소·전화번호 꼬리물기 추적을 스트리밍한다."""
    cfg = settings or Settings.load()
    if not cfg.has_gemini:
        yield {
            "event": "error",
            "message": "2단계 세부주소 추적을 실행하려면 GEMINI_API_KEY가 필요합니다. 우측 상단 [API · 시트 설정] 또는 .env 파일에 발급받은 키를 입력해 주세요.",
        }
        return
    yield from run_live_investigation(clue, cfg)


"""FarmFinder 요청/응답 및 1단계(지역 대표 발굴) · 2단계(세부주소 추적) 데이터 스키마."""

from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class DiscoveryInput(BaseModel):
    """1단계 초기 조건: 어떤 지역에 어떤 품목인지 입력받아 대표 작목반·대표 농가를 발굴한다."""

    region: str = Field(default="", description="조사할 지역 (예: 경북 의성, 충남 논산, 전남 해남)")
    crop_item: str = Field(default="", description="조사할 품목명 (예: 사과, 딸기, 고구마)")
    focus_hint: str = Field(default="", description="선택 조건 (예: 수출 작목반, 공선출하회, 친환경/GAP, 대농가)")
    max_steps: int = Field(default=4, ge=2, le=8, description="1단계 발굴용 최대 도구 호출 라운드 수")


class RegionalLeaderItem(BaseModel):
    """1단계에서 발굴된 지역 대표 작목반·공선회·선도농가·영농법인 항목."""

    leader_type: str = Field(
        default="대표 작목반",
        description="대표 작목반 | 공선출하회·연구회 | 대표·선도 농가 | 영농조합·법인",
    )
    crop_group_name: str = Field(default="", description="작목반/출하회/단체명")
    farm_name: str = Field(default="", description="농장명 또는 법인명")
    representative: str = Field(default="", description="회장/반장/대표자/선도농가 성명")
    crop_item: str = Field(default="", description="품목명")
    sub_region: str = Field(default="", description="확인된 읍·면·동 수준 권역 주소")
    scale_or_badge: str = Field(default="", description="규모·인증·수상 등 핵심 특징 태그")
    summary: str = Field(default="", description="대표 작목반/농가로 선정된 근거 요약")
    phone: str = Field(default="", description="1단계에서 바로 확인된 대표 연락처(있을 경우)")
    source_title: str = Field(default="", description="근거 출처 제목")
    source_url: str = Field(default="", description="근거 출처 URL")
    source_type: str = Field(default="구글검색", description="구글검색 | 네이버뉴스 | GAP인증 | aT_FIS 등")


class TraceStep(BaseModel):
    step_no: int
    tool_name: str
    tool_label: str
    query_summary: str
    findings_summary: str
    urls: List[str] = Field(default_factory=list)


class DiscoveryResult(BaseModel):
    """1단계 지역 대표 작목반·대표 농가 발굴 결과."""

    mode_used: str = "live"
    region: str = ""
    crop_item: str = ""
    overview_summary: str = Field(default="", description="해당 지역·품목의 주산지 읍·면 및 생산조직 현황 요약")
    leaders: List[RegionalLeaderItem] = Field(default_factory=list)
    trace_steps: List[TraceStep] = Field(default_factory=list)


class ClueInput(BaseModel):
    """2단계 세부주소 꼬리물기 탐색 입력 (1단계에서 선택한 대상 또는 직접 입력)."""

    region: str = Field(default="", description="시·군·읍·면·리 등 알고 있는 지역 단서")
    crop_item: str = Field(default="", description="재배 품목명 (예: 사과, 딸기, 고구마)")
    crop_group: str = Field(default="", description="작목반명 또는 영농조합·단체명")
    farm_or_person: str = Field(default="", description="농가명, 농장명, 또는 대표자 성명")
    representative: str = Field(default="", description="1단계 등에서 이미 파악된 대표자 성명 (선택)")
    extra_clue: str = Field(default="", description="추가 단서 또는 연속 탐색 힌트")
    prior_clues: List[str] = Field(default_factory=list, description="이전 탐색에서 확보한 단서 목록")
    max_steps: int = Field(default=6, ge=2, le=10, description="최대 꼬리물기 도구 호출 라운드 수")

    def summary_text(self) -> str:
        parts = []
        if self.region.strip():
            parts.append(f"지역:{self.region.strip()}")
        if self.crop_item.strip():
            parts.append(f"품목:{self.crop_item.strip()}")
        if self.crop_group.strip():
            parts.append(f"작목반:{self.crop_group.strip()}")
        if self.farm_or_person.strip():
            parts.append(f"농가/농장:{self.farm_or_person.strip()}")
        if self.representative.strip() and self.representative.strip() != self.farm_or_person.strip():
            parts.append(f"대표:{self.representative.strip()}")
        if self.extra_clue.strip():
            parts.append(f"추가단서:{self.extra_clue.strip()}")
        return " | ".join(parts) if parts else "미지정 단서"


class SourceReference(BaseModel):
    title: str = ""
    url: str = ""
    source_type: str = Field(default="웹문서", description="구글검색 | 네이버뉴스 | 네이버블로그 | GAP인증 | aT_FIS | 도로명주소API")
    matched_info: str = Field(default="", description="해당 출처에서 확인된 핵심 단서")


class DiscoveredClue(BaseModel):
    step: int = 1
    clue_type: str = Field(default="단서", description="읍/면 | 리/번지 | 농장/법인명 | 대표자명 | 전화번호 | 인증정보")
    value: str = ""
    found_via: str = ""


class FarmCandidate(BaseModel):
    crop_group_name: str = Field(default="", description="작목반 또는 생산자단체명")
    farm_name: str = Field(default="", description="농장명 또는 영농조합/농업회사법인명")
    representative: str = Field(default="", description="대표자명 또는 농가주 성명")
    crop_item: str = Field(default="", description="주요 재배 품목")
    road_address: str = Field(default="", description="도로명 세부주소")
    jibun_address: str = Field(default="", description="지번 세부주소 (~읍/면 ~리 번지)")
    postal_code: str = Field(default="", description="우편번호")
    phone: str = Field(default="", description="전화번호 (블로그/법인DB 등에서 확인된 연락처)")
    address_precision: str = Field(
        default="미확인",
        description="세부번지 확정 | 리 단위 확인 | 읍·면 단위 확인 | 시·군 단위 확인 | 미확인",
    )
    verification_status: str = Field(
        default="추가확인 필요",
        description="교차검증 완료 | 단일출처 확인 | 추가확인 필요",
    )
    confidence: int = Field(default=50, ge=0, le=100)
    reasoning_summary: str = Field(default="", description="단서 연결 및 교차검증 근거 요약")
    sources: List[SourceReference] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    record_id: Optional[int] = None
    mode_used: str = "live"
    input_summary: str = ""
    best_match: FarmCandidate
    candidates: List[FarmCandidate] = Field(default_factory=list)
    discovered_clues: List[DiscoveredClue] = Field(default_factory=list)
    trace_steps: List[TraceStep] = Field(default_factory=list)
    follow_up_suggestions: List[str] = Field(default_factory=list)


class SheetSavePayload(BaseModel):
    record_id: Optional[int] = None
    input_summary: str = ""
    crop_group_name: str = ""
    farm_name: str = ""
    representative: str = ""
    crop_item: str = ""
    road_address: str = ""
    jibun_address: str = ""
    postal_code: str = ""
    phone: str = ""
    verification_status: str = ""
    reasoning_summary: str = ""
    source_urls: str = ""
    memo: str = ""


class ConfigUpdatePayload(BaseModel):
    updates: Dict[str, str] = Field(default_factory=dict)

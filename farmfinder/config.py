"""환경 변수(.env) 로드·저장 및 API 연결 상태 관리."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ENV_PATH = BASE_DIR / ".env"
DEFAULT_SHEET_ID = "1T26pUoB3u77KnELhB536r8vhsthrhAf1X14igLfGZdA"

EDITABLE_KEYS = [
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_SEARCH_MODEL",
    "SERPER_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "DATA_GO_KR_API_KEY",
    "GAP_API_URL",
    "JUSO_API_KEY",
    "GOOGLE_SHEET_ID",
    "GOOGLE_SHEET_TAB",
    "GOOGLE_APPS_SCRIPT_WEBHOOK_URL",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
]


def load_dotenv_file(path: Path = ENV_PATH) -> Dict[str, str]:
    """외부 패키지 의존성 없이 .env 파일을 파싱해 환경 변수에 반영한다."""
    parsed: Dict[str, str] = {}
    if not path.exists():
        return parsed

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        parsed[key] = value
        if key not in os.environ or os.environ[key] == "":
            os.environ[key] = value
    return parsed


@dataclass
class Settings:
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_search_model: str = "gemini-2.5-flash"
    serper_api_key: str = ""
    naver_client_id: str = ""
    naver_client_secret: str = ""
    data_go_kr_api_key: str = ""
    gap_api_url: str = "https://apis.data.go.kr/1543145/GAPCERTINFO/getCertDataList"
    juso_api_key: str = ""
    google_sheet_id: str = DEFAULT_SHEET_ID
    google_sheet_tab: str = "탐색결과"
    google_apps_script_webhook_url: str = ""
    google_service_account_file: str = ""

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv_file()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        search_model = os.getenv("GEMINI_SEARCH_MODEL", "").strip() or gemini_model
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=gemini_model,
            gemini_search_model=search_model,
            serper_api_key=os.getenv("SERPER_API_KEY", "").strip(),
            naver_client_id=os.getenv("NAVER_CLIENT_ID", "").strip(),
            naver_client_secret=os.getenv("NAVER_CLIENT_SECRET", "").strip(),
            data_go_kr_api_key=os.getenv("DATA_GO_KR_API_KEY", "").strip(),
            gap_api_url=(
                os.getenv("GAP_API_URL", "").strip()
                or "https://apis.data.go.kr/1543145/GAPCERTINFO/getCertDataList"
            ),
            juso_api_key=os.getenv("JUSO_API_KEY", "").strip(),
            google_sheet_id=os.getenv("GOOGLE_SHEET_ID", "").strip() or DEFAULT_SHEET_ID,
            google_sheet_tab=os.getenv("GOOGLE_SHEET_TAB", "").strip() or "탐색결과",
            google_apps_script_webhook_url=os.getenv("GOOGLE_APPS_SCRIPT_WEBHOOK_URL", "").strip(),
            google_service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip(),
        )

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_naver(self) -> bool:
        return bool(self.naver_client_id and self.naver_client_secret)

    @property
    def has_gap(self) -> bool:
        return bool(self.data_go_kr_api_key)

    @property
    def has_juso(self) -> bool:
        return bool(self.juso_api_key)

    @property
    def has_sheets(self) -> bool:
        if self.google_apps_script_webhook_url:
            return True
        if self.google_service_account_file:
            sa_path = Path(self.google_service_account_file)
            if not sa_path.is_absolute():
                sa_path = BASE_DIR / sa_path
            return sa_path.exists()
        return False

    @property
    def sheet_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.google_sheet_id}/edit"

    def status_summary(self) -> dict:
        def _mask(secret: str) -> str:
            if not secret:
                return ""
            if len(secret) <= 8:
                return "*" * len(secret)
            return f"{secret[:4]}...{secret[-4:]}"

        return {
            "ready_for_live": self.has_gemini,
            "apis": {
                "gemini": {
                    "configured": self.has_gemini,
                    "masked": _mask(self.gemini_api_key),
                    "model": self.gemini_model,
                    "search_model": self.gemini_search_model,
                },
                "google_search": {
                    "configured": self.has_gemini or bool(self.serper_api_key),
                    "mode": (
                        "Gemini Grounding + Serper"
                        if (self.has_gemini and self.serper_api_key)
                        else ("Gemini Grounding + Google News RSS" if self.has_gemini else "Google News RSS")
                    ),
                },
                "naver": {
                    "configured": self.has_naver,
                    "masked": _mask(self.naver_client_id),
                },
                "gap": {
                    "configured": self.has_gap,
                    "masked": _mask(self.data_go_kr_api_key),
                    "url": self.gap_api_url,
                },
                "juso": {
                    "configured": self.has_juso,
                    "masked": _mask(self.juso_api_key),
                },
                "atfis": {
                    "configured": True,
                    "mode": "공개 페이지 실시간 조회 + 로컬 DB",
                },
                "sheets": {
                    "configured": self.has_sheets,
                    "sheet_id": self.google_sheet_id,
                    "sheet_tab": self.google_sheet_tab,
                    "sheet_url": self.sheet_url,
                    "webhook_configured": bool(self.google_apps_script_webhook_url),
                    "service_account_configured": bool(self.google_service_account_file),
                },
            },
        }


def save_env_updates(updates: Dict[str, str], path: Path = ENV_PATH) -> Settings:
    """사용자가 설정 모달에서 입력한 값들을 .env 및 현재 프로세스 환경변수에 반영한다."""
    existing: Dict[str, str] = {}
    if path.exists():
        existing = load_dotenv_file(path)

    for key, value in updates.items():
        if key not in EDITABLE_KEYS or value is None:
            continue
        cleaned = value.strip()
        # 빈 문자열이 넘어왔을 때는 명시적 초기화 의도가 아니면 기존 키를 보존할 수 있도록 처리
        existing[key] = cleaned
        os.environ[key] = cleaned

    lines = [
        "# FarmFinder 환경 설정 (.env)",
        "# UI 설정 창 또는 직접 편집으로 관리됩니다.",
        "",
    ]
    for key in EDITABLE_KEYS:
        val = existing.get(key, os.getenv(key, ""))
        lines.append(f"{key}={val}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Settings.load()

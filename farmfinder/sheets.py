"""Google Sheets 저장 연동 모듈 (Apps Script 웹훅 방식 + Google Service Account JWT 방식 동시 지원)."""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .config import BASE_DIR, Settings
from .models import SheetSavePayload
from .storage import SHEET_HEADERS
from .tools.http_client import http_get_text, http_post_json

APPS_SCRIPT_GUIDE_CODE = """function doPost(e) {
  var body = JSON.parse(e.postData.contents);
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tabName = body.tab || '탐색결과';
  var sheet = ss.getSheetByName(tabName) || ss.insertSheet(tabName);
  if (sheet.getLastRow() === 0 && body.headers) {
    sheet.appendRow(body.headers);
    sheet.getRange(1, 1, 1, body.headers.length).setFontWeight('bold');
  }
  sheet.appendRow(body.row);
  return ContentService.createTextOutput(JSON.stringify({ ok: true, rowNumber: sheet.getLastRow() }))
    .setMimeType(ContentService.MimeType.JSON);
}"""


def payload_to_row(payload: SheetSavePayload) -> List[str]:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        now_str,
        payload.input_summary,
        payload.crop_group_name,
        payload.farm_name,
        payload.representative,
        payload.crop_item,
        payload.road_address,
        payload.jibun_address,
        payload.postal_code,
        payload.phone,
        payload.verification_status,
        payload.reasoning_summary,
        payload.source_urls,
        payload.memo,
    ]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _get_service_account_access_token(sa_path: Path) -> str:
    """cryptography 패키지를 이용해 외부 SDK 없이 서비스 계정 OAuth2 액세스 토큰을 발급받는다."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    creds = json.loads(sa_path.read_text(encoding="utf-8"))
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    claim = _b64url(
        json.dumps(
            {
                "iss": creds["client_email"],
                "scope": "https://www.googleapis.com/auth/spreadsheets",
                "aud": creds.get("token_uri", "https://oauth2.googleapis.com/token"),
                "iat": now,
                "exp": now + 3600,
            }
        ).encode("utf-8")
    )
    signing_input = f"{header}.{claim}".encode("ascii")
    private_key = serialization.load_pem_private_key(creds["private_key"].encode("utf-8"), password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    jwt_assertion = f"{header}.{claim}.{_b64url(signature)}"

    token_uri = creds.get("token_uri", "https://oauth2.googleapis.com/token")
    status, data, raw_text = http_post_json(
        token_uri,
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_assertion,
        },
        timeout=15.0,
    )
    if status != 200 or "access_token" not in data:
        raise RuntimeError(f"Google OAuth 토큰 발급 실패 (HTTP {status}): {raw_text[:200]}")
    return str(data["access_token"])


def _append_via_service_account(row: List[str], settings: Settings) -> Dict[str, Any]:
    sa_path = Path(settings.google_service_account_file)
    if not sa_path.is_absolute():
        sa_path = BASE_DIR / sa_path
    if not sa_path.exists():
        return {"ok": False, "error": f"서비스 계정 파일을 찾을 수 없습니다: {sa_path}"}

    token = _get_service_account_access_token(sa_path)
    auth_headers = {"Authorization": f"Bearer {token}"}
    sheet_id = urllib.parse.quote(settings.google_sheet_id)

    # 첫 번째 시트 또는 지정 탭의 1행 헤더 유무 확인 (탭이 없으면 기본 첫 시트 A1 사용)
    tab_range = urllib.parse.quote(f"{settings.google_sheet_tab}!A1:N1")
    check_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{tab_range}"
    status, check_body, _ = http_get_text(check_url, headers=auth_headers, timeout=12.0)

    target_range = f"{settings.google_sheet_tab}!A1"
    if status == 400:
        # 지정 탭 이름이 아직 없으면 기본 첫 시트(A1:N1) 사용
        target_range = "A1"
        check_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1:N1"
        status, check_body, _ = http_get_text(check_url, headers=auth_headers, timeout=12.0)

    rows_to_append: List[List[str]] = []
    if status == 200:
        try:
            existing_vals = json.loads(check_body).get("values", [])
            if not existing_vals:
                rows_to_append.append(SHEET_HEADERS)
        except json.JSONDecodeError:
            pass

    rows_to_append.append(row)
    append_url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/"
        f"{urllib.parse.quote(target_range)}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    )
    app_status, app_data, app_raw = http_post_json(
        append_url,
        {"values": rows_to_append},
        headers=auth_headers,
        timeout=15.0,
    )
    if app_status != 200:
        return {"ok": False, "error": f"Google Sheets API 오류 (HTTP {app_status}): {app_raw[:220]}"}

    updated_range = app_data.get("updates", {}).get("updatedRange", "")
    return {
        "ok": True,
        "method": "service_account",
        "updated_range": updated_range,
        "sheet_url": settings.sheet_url,
    }


def _append_via_webhook(row: List[str], settings: Settings) -> Dict[str, Any]:
    status, data, raw_text = http_post_json(
        settings.google_apps_script_webhook_url,
        {
            "tab": settings.google_sheet_tab,
            "headers": SHEET_HEADERS,
            "row": row,
        },
        timeout=20.0,
    )
    if status in (200, 201) and (data.get("ok") or "ok" in raw_text.lower()):
        return {
            "ok": True,
            "method": "apps_script_webhook",
            "row_number": data.get("rowNumber"),
            "sheet_url": settings.sheet_url,
        }
    return {
        "ok": False,
        "error": f"Apps Script 웹훅 응답 오류 (HTTP {status}): {raw_text[:200]}",
    }


def append_to_google_sheet(payload: SheetSavePayload, settings: Settings | None = None) -> Dict[str, Any]:
    """FarmFinder Google 시트에 탐색 결과 1행을 추가한다."""
    cfg = settings or Settings.load()
    row = payload_to_row(payload)

    if cfg.google_apps_script_webhook_url:
        return _append_via_webhook(row, cfg)

    if cfg.google_service_account_file:
        try:
            return _append_via_service_account(row, cfg)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    return {
        "ok": False,
        "needs_setup": True,
        "sheet_url": cfg.sheet_url,
        "error": (
            "로컬 DB에는 저장되었으나 Google 시트 쓰기 인증(Apps Script 웹훅 URL 또는 서비스 계정 JSON)이 "
            "아직 설정되지 않았습니다. 우측 상단 [API · 시트 설정]에서 1분 만에 연결할 수 있습니다."
        ),
    }

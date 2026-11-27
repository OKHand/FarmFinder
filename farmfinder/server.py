"""FastAPI 백엔드 서버 (실시간 SSE 탐색 스트리밍, Google Sheets 저장, CSV 입출력, 정적 UI 서빙)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent import stream_investigation, stream_regional_discovery
from .config import BASE_DIR, DATA_DIR, EDITABLE_KEYS, Settings, load_dotenv_file, save_env_updates
from .models import ClueInput, ConfigUpdatePayload, DiscoveryInput, SheetSavePayload
from .sheets import APPS_SCRIPT_GUIDE_CODE, append_to_google_sheet
from .storage import (
    export_records_csv,
    get_record_detail,
    list_recent_records,
    update_record_from_payload,
)

STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="FarmFinder",
    description="2단계(지역·품목 대표 발굴 → 세부주소 꼬리물기) 작목반·농가 탐색기",
    version="0.2.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/status")
def get_status() -> dict:
    cfg = Settings.load()
    summary = cfg.status_summary()
    summary["apps_script_code"] = APPS_SCRIPT_GUIDE_CODE
    local_csvs = [p.name for p in sorted(DATA_DIR.glob("*.csv"))]
    summary["local_csv_files"] = local_csvs
    return summary


@app.post("/api/config")
def update_config(payload: ConfigUpdatePayload) -> dict:
    existing = load_dotenv_file()
    merged = {}
    for k in EDITABLE_KEYS:
        merged[k] = existing.get(k, os.getenv(k, ""))

    for k, v in payload.updates.items():
        if k not in EDITABLE_KEYS or v is None:
            continue
        # 설정 창에서 시크릿 입력란을 비워둔 채 저장하면 기존 저장 키를 유지 ("__CLEAR__" 전달 시 삭제)
        if v == "__CLEAR__":
            merged[k] = ""
        elif v.strip() != "":
            merged[k] = v.strip()

    cfg = save_env_updates(merged)
    res = cfg.status_summary()
    res["apps_script_code"] = APPS_SCRIPT_GUIDE_CODE
    return res


@app.post("/api/discover/stream")
def discover_stream(req: DiscoveryInput) -> StreamingResponse:
    cfg = Settings.load()

    def event_generator() -> Iterator[str]:
        try:
            for item in stream_regional_discovery(req, cfg):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except Exception as exc:
            err = {"event": "error", "message": f"1단계 발굴 중 예외가 발생했습니다: {exc}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/investigate/stream")
def investigate_stream(clue: ClueInput) -> StreamingResponse:
    cfg = Settings.load()

    def event_generator() -> Iterator[str]:
        try:
            for item in stream_investigation(clue, cfg):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except Exception as exc:
            err = {"event": "error", "message": f"탐색 중 예외가 발생했습니다: {exc}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
def get_history() -> dict:
    return {"records": list_recent_records(limit=40)}


@app.get("/api/history/{record_id}")
def get_history_item(record_id: int) -> dict:
    detail = get_record_detail(record_id)
    if not detail:
        raise HTTPException(status_code=404, detail="해당 탐색 이력을 찾을 수 없습니다.")
    return detail


@app.post("/api/sheets/save")
def save_to_sheet(payload: SheetSavePayload) -> dict:
    cfg = Settings.load()
    sheet_res = append_to_google_sheet(payload, cfg)
    record_id = update_record_from_payload(payload, synced=bool(sheet_res.get("ok")))
    sheet_res["record_id"] = record_id
    return sheet_res


@app.get("/api/export/csv")
def download_csv() -> Response:
    csv_bytes = export_records_csv()
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="farmfinder_results.csv"'},
    )


@app.post("/api/upload/csv")
async def upload_reference_csv(kind: str = "gap", file: UploadFile = File(...)) -> dict:
    """GAP 인증농가 현황 CSV 또는 aT FIS 생산자 CSV를 data/ 폴더에 업로드하여 초고속 로컬 검색에 활용한다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    safe_base = re.sub(r"[^가-힣a-zA-Z0-9._-]", "_", file.filename or "reference.csv")
    prefix = "gap_" if kind.lower() == "gap" else "atfis_"
    if not safe_base.lower().startswith(prefix):
        safe_base = prefix + safe_base
    if not safe_base.lower().endswith(".csv"):
        safe_base += ".csv"

    dest: Path = DATA_DIR / safe_base
    raw = await file.read()
    dest.write_bytes(raw)
    return {
        "ok": True,
        "saved_as": dest.name,
        "size_kb": round(len(raw) / 1024, 1),
    }

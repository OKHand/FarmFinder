"""로컬 SQLite 탐색 이력 저장소 및 한글 엑셀 호환 CSV 내보내기."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR
from .models import InvestigationResult, SheetSavePayload

DB_PATH = DATA_DIR / "farmfinder.db"

SHEET_HEADERS = [
    "저장일시",
    "입력단서",
    "작목반/단체명",
    "농장/법인명",
    "대표자명",
    "품목",
    "도로명 세부주소",
    "지번주소(읍면리)",
    "우편번호",
    "전화번호",
    "교차검증 상태",
    "탐색 근거 요약",
    "출처 URL",
    "비고",
]


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            mode_used TEXT NOT NULL,
            input_summary TEXT NOT NULL,
            crop_group_name TEXT DEFAULT '',
            farm_name TEXT DEFAULT '',
            representative TEXT DEFAULT '',
            crop_item TEXT DEFAULT '',
            road_address TEXT DEFAULT '',
            jibun_address TEXT DEFAULT '',
            postal_code TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            verification_status TEXT DEFAULT '',
            confidence INTEGER DEFAULT 0,
            reasoning_summary TEXT DEFAULT '',
            source_urls TEXT DEFAULT '',
            memo TEXT DEFAULT '',
            synced_to_sheet_at TEXT DEFAULT '',
            raw_result_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_investigation(result: InvestigationResult, db_path: Path = DB_PATH) -> int:
    """탐색 완료 결과를 SQLite에 저장하고 생성된 record_id를 반환한다."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bm = result.best_match
    urls_str = "\n".join(s.url for s in bm.sources if s.url)
    raw_json = result.model_dump_json()

    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO search_records (
                created_at, mode_used, input_summary,
                crop_group_name, farm_name, representative, crop_item,
                road_address, jibun_address, postal_code, phone,
                verification_status, confidence, reasoning_summary,
                source_urls, raw_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_str,
                result.mode_used,
                result.input_summary,
                bm.crop_group_name,
                bm.farm_name,
                bm.representative,
                bm.crop_item,
                bm.road_address,
                bm.jibun_address,
                bm.postal_code,
                bm.phone,
                bm.verification_status,
                bm.confidence,
                bm.reasoning_summary,
                urls_str,
                raw_json,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_record_from_payload(
    payload: SheetSavePayload,
    synced: bool = False,
    db_path: Path = DB_PATH,
) -> int:
    """사용자가 화면에서 보정한 값과 시트 동기화 여부를 로컬 DB에도 반영한다."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    synced_ts = now_str if synced else ""

    with _connect(db_path) as conn:
        if payload.record_id:
            if synced:
                conn.execute(
                    """
                    UPDATE search_records SET
                        crop_group_name = ?, farm_name = ?, representative = ?, crop_item = ?,
                        road_address = ?, jibun_address = ?, postal_code = ?, phone = ?,
                        verification_status = ?, reasoning_summary = ?, source_urls = ?,
                        memo = ?, synced_to_sheet_at = ?
                    WHERE id = ?
                    """,
                    (
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
                        synced_ts,
                        payload.record_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE search_records SET
                        crop_group_name = ?, farm_name = ?, representative = ?, crop_item = ?,
                        road_address = ?, jibun_address = ?, postal_code = ?, phone = ?,
                        verification_status = ?, reasoning_summary = ?, source_urls = ?, memo = ?
                    WHERE id = ?
                    """,
                    (
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
                        payload.record_id,
                    ),
                )
            conn.commit()
            return payload.record_id

        cur = conn.execute(
            """
            INSERT INTO search_records (
                created_at, mode_used, input_summary,
                crop_group_name, farm_name, representative, crop_item,
                road_address, jibun_address, postal_code, phone,
                verification_status, reasoning_summary, source_urls,
                memo, synced_to_sheet_at, raw_result_json
            ) VALUES (?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
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
                synced_ts,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_recent_records(limit: int = 30, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, mode_used, input_summary, crop_group_name, farm_name,
                   representative, crop_item, road_address, jibun_address, postal_code,
                   phone, verification_status, confidence, reasoning_summary,
                   source_urls, memo, synced_to_sheet_at
            FROM search_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_record_detail(record_id: int, db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM search_records WHERE id = ?", (record_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["raw_result"] = json.loads(d.pop("raw_result_json") or "{}")
        except json.JSONDecodeError:
            d["raw_result"] = {}
        return d


def export_records_csv(db_path: Path = DB_PATH) -> bytes:
    """저장된 전체 탐색 이력을 한글 엑셀에서 깨지지 않는 UTF-8 BOM CSV 바이트로 반환한다."""
    rows = list_recent_records(limit=1000, db_path=db_path)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(SHEET_HEADERS)
    for r in reversed(rows):
        writer.writerow(
            [
                r.get("created_at", ""),
                r.get("input_summary", ""),
                r.get("crop_group_name", ""),
                r.get("farm_name", ""),
                r.get("representative", ""),
                r.get("crop_item", ""),
                r.get("road_address", ""),
                r.get("jibun_address", ""),
                r.get("postal_code", ""),
                r.get("phone", ""),
                r.get("verification_status", ""),
                r.get("reasoning_summary", ""),
                r.get("source_urls", ""),
                r.get("memo", ""),
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")

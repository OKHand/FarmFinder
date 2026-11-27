"""FarmFinder 핵심 모듈, 파서, 라이브 에이전트 가드, 로컬 DB 및 CSV 입출력 단위 테스트."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from farmfinder.agent import stream_investigation, stream_regional_discovery
from farmfinder.config import Settings
from farmfinder.models import (
    ClueInput,
    DiscoveredClue,
    DiscoveryInput,
    FarmCandidate,
    InvestigationResult,
    SheetSavePayload,
    SourceReference,
    TraceStep,
)
from farmfinder.sheets import payload_to_row
from farmfinder.storage import (
    export_records_csv,
    get_record_detail,
    list_recent_records,
    save_investigation,
    update_record_from_payload,
)
from farmfinder.tools.gap_api import _format_gap_item, _parse_gap_response
from farmfinder.tools.http_client import extract_korean_clues_from_text, html_to_clean_text
from farmfinder.tools.juso_api import _sanitize_juso_keyword
from farmfinder.tools.webpage_reader import _normalize_naver_blog_url


class FarmFinderUnitTests(unittest.TestCase):
    def test_naver_blog_mobile_rewrite(self) -> None:
        desktop = "https://blog.naver.com/applefarm99/223456789012"
        self.assertEqual(
            _normalize_naver_blog_url(desktop),
            "https://m.blog.naver.com/applefarm99/223456789012",
        )
        postview = "https://blog.naver.com/PostView.naver?blogId=applefarm99&logNo=223456789012"
        self.assertEqual(
            _normalize_naver_blog_url(postview),
            "https://m.blog.naver.com/applefarm99/223456789012",
        )

    def test_korean_phone_and_address_scanner(self) -> None:
        html_doc = """
        <html><head><title>농원 직거래 안내</title></head>
        <body>
          <p>경상북도 의성군 봉양면 도원리 412-1 과수원입니다.</p>
          <p>주문 문의: 010-5321-9876 / 선별장 054-832-1234</p>
        </body></html>
        """
        title, text = html_to_clean_text(html_doc)
        self.assertEqual(title, "농원 직거래 안내")
        clues = extract_korean_clues_from_text(text)
        self.assertIn("010-5321-9876", clues["phones"])
        self.assertIn("054-832-1234", clues["phones"])
        self.assertTrue(any("의성군 봉양면 도원리" in a for a in clues["address_candidates"]))

    def test_gap_xml_and_json_parser(self) -> None:
        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <response><body><items>
          <item>
            <certNo>1029368</certNo>
            <prdcrGrpNm>온누리새싹원</prdcrGrpNm>
            <rprsntvNm>홍상표</rprsntvNm>
            <itemNm>돌미나리</itemNm>
            <grpStdgCdNm>세종특별자치시 전의면 서정리</grpStdgCdNm>
          </item>
        </items></body></response>"""
        rows = _parse_gap_response(sample_xml)
        self.assertEqual(len(rows), 1)
        formatted = _format_gap_item(rows[0])
        self.assertEqual(formatted["group_name"], "온누리새싹원")
        self.assertEqual(formatted["representative"], "홍상표")
        self.assertIn("서정리", formatted["address"])

    def test_juso_keyword_sanitizer(self) -> None:
        dirty = "경북 의성군 봉양면 도원리=412 OR SELECT"
        cleaned = _sanitize_juso_keyword(dirty)
        self.assertNotIn("=", cleaned)
        self.assertNotIn("SELECT", cleaned)
        self.assertIn("봉양면 도원리", cleaned)

    def test_live_streams_require_api_key_without_mock_fallback(self) -> None:
        empty_cfg = Settings(gemini_api_key="")
        disc_events = list(stream_regional_discovery(DiscoveryInput(region="충남 논산", crop_item="딸기"), empty_cfg))
        self.assertEqual(len(disc_events), 1)
        self.assertEqual(disc_events[0]["event"], "error")
        self.assertIn("GEMINI_API_KEY", disc_events[0]["message"])

        inv_events = list(stream_investigation(ClueInput(region="충남 논산", crop_item="딸기"), empty_cfg))
        self.assertEqual(len(inv_events), 1)
        self.assertEqual(inv_events[0]["event"], "error")
        self.assertIn("GEMINI_API_KEY", inv_events[0]["message"])

    def test_sqlite_storage_and_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "test.db"
            res_model = InvestigationResult(
                mode_used="live",
                input_summary="지역:충남 논산시 광석면 | 품목:딸기",
                best_match=FarmCandidate(
                    crop_group_name="광석딸기공선출하회",
                    farm_name="다솜농원",
                    representative="홍길동",
                    crop_item="딸기",
                    road_address="충청남도 논산시 광석면 광석로 1",
                    jibun_address="충청남도 논산시 광석면 율리 1",
                    postal_code="32900",
                    phone="041-730-0000",
                    address_precision="세부번지 확정",
                    verification_status="교차검증 완료",
                    confidence=90,
                    reasoning_summary="실제 API 교차 확인",
                    sources=[SourceReference(title="GAP", url="https://www.gap.go.kr", source_type="GAP인증")],
                ),
                discovered_clues=[DiscoveredClue(step=1, clue_type="리/번지", value="율리 1", found_via="GAP")],
                trace_steps=[
                    TraceStep(
                        step_no=1,
                        tool_name="search_gap",
                        tool_label="GAP 인증농가 OpenAPI 조회",
                        query_summary="딸기 / 논산시",
                        findings_summary="매칭 1건",
                    )
                ],
            )
            rec_id = save_investigation(res_model, db_path=tmp_db)
            self.assertGreater(rec_id, 0)
            self.assertEqual(len(list_recent_records(db_path=tmp_db)), 1)

            payload = SheetSavePayload(
                record_id=rec_id,
                input_summary=res_model.input_summary,
                crop_group_name="광석딸기공선출하회",
                farm_name="다솜농원",
                representative="홍길동",
                crop_item="딸기",
                road_address="충청남도 논산시 광석면 광석로 1",
                jibun_address="충청남도 논산시 광석면 율리 1",
                postal_code="32900",
                phone="041-730-0000",
                verification_status="교차검증 완료",
                reasoning_summary="실제 API 교차 확인",
                source_urls="https://www.gap.go.kr",
                memo="검수 완료",
            )
            update_record_from_payload(payload, synced=True, db_path=tmp_db)
            detail = get_record_detail(rec_id, db_path=tmp_db)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["memo"], "검수 완료")
            self.assertEqual(len(payload_to_row(payload)), 14)

            csv_bytes = export_records_csv(db_path=tmp_db)
            self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
            self.assertIn("광석딸기공선출하회", csv_bytes.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()

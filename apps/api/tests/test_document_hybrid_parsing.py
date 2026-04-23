import unittest

from routers import document as document_router


class DocumentHybridParsingTest(unittest.TestCase):
    def test_select_pdf_fallback_pages_uses_conservative_rules(self) -> None:
        pages = [
            {
                "page": 1,
                "markdown": "1. 모집공고\n신청 일정 안내\n2026.04.22 ~ 2026.04.24",
                "text_length": 34,
                "line_count": 3,
            },
            {
                "page": 2,
                "markdown": (
                    "주택타입임대조건전환 가능 보증금한도액최대전환시 임대조건최대거주기간(년)"
                    "임대보증금월 임대료 임 대 보 증 금월 임대료계계약금잔금46A171,200,0008,560,000"
                    "162,640,000727,60049A182,000,0009,100,000172,900,000773,50055B206,000,000"
                    "10,300,000195,700,000875,500"
                ),
                "text_length": 201,
                "line_count": 1,
            },
            {
                "page": 3,
                "markdown": "",
                "text_length": 0,
                "line_count": 0,
            },
        ]

        fallback_pages = document_router.select_pdf_fallback_pages(pages)

        self.assertEqual(fallback_pages, [2, 3])

    def test_merge_pdf_pages_replaces_only_fallback_pages(self) -> None:
        general_pages = [
            {"page": 1, "markdown": "page one general"},
            {"page": 2, "markdown": "page two general"},
            {"page": 3, "markdown": "page three general"},
        ]
        upstage_pages = {
            2: "page two upstage",
        }

        merged_pages, merged_markdown = document_router.merge_pdf_pages(
            general_pages, upstage_pages
        )

        self.assertEqual(
            [page["source"] for page in merged_pages],
            ["general", "upstage", "general"],
        )
        self.assertEqual(merged_pages[1]["markdown"], "page two upstage")
        self.assertIn("page one general", merged_markdown)
        self.assertIn("page two upstage", merged_markdown)
        self.assertIn("page three general", merged_markdown)

    def test_build_parse_cache_data_includes_general_and_upstage_results(self) -> None:
        cache_data = document_router.build_parse_cache_data(
            document_id="doc-1",
            filename="notice.pdf",
            file_type="pdf",
            file_size=1234,
            markdown="merged markdown",
            file_hash="abc123",
            general_parse={
                "parser": "pypdf",
                "pages": [{"page": 1, "markdown": "general page"}],
            },
            hybrid_parse={
                "fallback_pages": [2],
                "upstage_pages": {2: "upstage page"},
                "pages": [
                    {"page": 1, "markdown": "general page", "source": "general"},
                    {"page": 2, "markdown": "upstage page", "source": "upstage"},
                ],
            },
            upstage_response={"elements": [{"page": 1}]},
        )

        self.assertEqual(cache_data["markdown"], "merged markdown")
        self.assertEqual(cache_data["file_hash"], "abc123")
        self.assertEqual(cache_data["general_parse"]["parser"], "pypdf")
        self.assertEqual(cache_data["hybrid_parse"]["fallback_pages"], [2])
        self.assertEqual(cache_data["upstage_response"], {"elements": [{"page": 1}]})


if __name__ == "__main__":
    unittest.main()

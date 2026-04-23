import unittest

from api.routers import document as document_router


class DocumentTableAwareParsingTest(unittest.TestCase):
    def test_table_rows_to_markdown_uses_first_row_as_header(self) -> None:
        rows = [
            ["공급형별", "주택타입", "건설호수"],
            ["일반", "46A", "68"],
            ["일반", "49A", "64"],
        ]

        markdown = document_router.table_rows_to_markdown(rows)

        self.assertIn("| 공급형별 | 주택타입 | 건설호수 |", markdown)
        self.assertIn("| 일반 | 46A | 68 |", markdown)
        self.assertIn("| 일반 | 49A | 64 |", markdown)

    def test_merge_page_text_and_tables_keeps_both_sections(self) -> None:
        markdown = document_router.merge_page_text_and_tables(
            "1. 공급일정\n신청 기간 안내",
            [
                "| 공급형별 | 주택타입 |\n| --- | --- |\n| 일반 | 46A |",
            ],
        )

        self.assertIn("1. 공급일정", markdown)
        self.assertIn("| 공급형별 | 주택타입 |", markdown)

    def test_build_parse_cache_data_can_mark_table_aware_strategy(self) -> None:
        cache_data = document_router.build_parse_cache_data(
            document_id="doc-2",
            filename="notice.pdf",
            file_type="pdf",
            file_size=4321,
            markdown="merged markdown",
            file_hash="hash-2",
            parse_strategy="pdf_table_aware",
            general_parse={
                "parser": "pdfplumber",
                "page_count": 2,
                "pages": [{"page": 1, "markdown": "page 1"}],
            },
        )

        self.assertEqual(cache_data["parse_strategy"], "pdf_table_aware")
        self.assertEqual(cache_data["general_parse"]["parser"], "pdfplumber")


if __name__ == "__main__":
    unittest.main()

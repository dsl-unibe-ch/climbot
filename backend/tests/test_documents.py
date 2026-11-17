import tempfile
import unittest
from pathlib import Path

import fitz

from app.core.source_files import extract_pdf_page, pdf_source_metadata, resolve_pdf


class PdfSourceTests(unittest.TestCase):
    def test_resolve_pdf_rejects_paths_outside_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "outside"):
                resolve_pdf(directory, "../outside.pdf")

    def test_pdf_metadata_reports_relative_path_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reports" / "report.pdf"
            path.parent.mkdir()
            path.write_bytes(b"pdf")

            metadata = pdf_source_metadata(root, "reports/report.pdf")

        self.assertEqual(
            metadata,
            {"source_path": "reports/report.pdf", "pdf_size": 3},
        )

    def test_extract_page_returns_only_requested_pdf_page(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            document = fitz.open()
            document.new_page().insert_text((72, 72), "First page")
            document.new_page().insert_text((72, 72), "Second page")
            document.save(path)
            document.close()

            extracted = extract_pdf_page(path, 1)

        page_document = fitz.open(stream=extracted, filetype="pdf")
        self.addCleanup(page_document.close)
        self.assertEqual(page_document.page_count, 1)
        self.assertIn("Second page", page_document.load_page(0).get_text())


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from docx import Document

from app.config import Settings

with patch(
    "app.config.get_settings",
    return_value=Settings.model_construct(
        azure_tenant_id="test", azure_client_id="test", openai_api_key="test"
    ),
):
    from app.core import ingestion


class IngestionManifestTests(unittest.TestCase):
    def test_latest_onedrive_export_is_selected_with_direct_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sharepoint = root / "sharepoint"
            old = sharepoint / "OneDrive_1_10-01-2026"
            latest = sharepoint / "OneDrive_1_11-13-2026"
            old.mkdir(parents=True)
            latest.mkdir()
            (old / "old.txt").write_text("old")
            (latest / "latest.txt").write_text("latest")
            (sharepoint / "direct.txt").write_text("direct")

            paths, export = ingestion._discover_supported_files(root)

        self.assertEqual(export, latest.name)
        self.assertEqual(
            {path.name for path in paths},
            {"latest.txt", "direct.txt"},
        )

    def test_exact_duplicates_keep_one_candidate_and_all_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.txt"
            second = root / "b.txt"
            first.write_text("same content")
            second.write_text("same content")

            candidates, _ = ingestion._build_manifest(root)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].path.name, "a.txt")
        self.assertEqual({path.name for path in candidates[0].aliases}, {"a.txt", "b.txt"})

    def test_equivalent_pdf_is_preferred_over_docx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "Climate impacts adaptation research programme"
            pdf_path = root / "report.pdf"
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((72, 72), text)
            pdf.save(pdf_path)
            pdf.close()
            document = Document()
            document.add_paragraph(text)
            document.save(root / "report.docx")

            candidates, _ = ingestion._build_manifest(root)

        self.assertEqual([candidate.path.name for candidate in candidates], ["report.pdf"])
        self.assertEqual(
            {path.name for path in candidates[0].aliases},
            {"report.pdf", "report.docx"},
        )

    def test_empty_pdf_falls_back_to_docx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = fitz.open()
            pdf.new_page()
            pdf.save(root / "report.pdf")
            pdf.close()
            document = Document()
            document.add_paragraph("Usable document text")
            document.save(root / "report.docx")

            candidates, _ = ingestion._build_manifest(root)

        self.assertEqual([candidate.path.name for candidate in candidates], ["report.docx"])


if __name__ == "__main__":
    unittest.main()

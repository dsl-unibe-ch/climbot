import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.core.index_state import read_index_state, write_index_state
from app.core.spreadsheets import (
    SpreadsheetWorkbook,
    rebuild_spreadsheet_index,
    search_spreadsheet_rows,
)


class SpreadsheetIndexTests(unittest.TestCase):
    def test_worksheets_are_catalogued_and_rows_are_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "contacts.xlsx"
            workbook = Workbook()
            active = workbook.active
            active.title = "WP1"
            active.append(["Name", "Institution"])
            active.append(["Ada Lovelace", "University of Bern"])
            active.append(["Grace Hopper", "ETH Zurich"])
            second = workbook.create_sheet("WP2")
            second.append(["Name", "Institution"])
            second.append(["Katherine Johnson", "University of Bern"])
            workbook.save(workbook_path)

            database = root / "spreadsheets.duckdb"
            sheets = rebuild_spreadsheet_index(
                database,
                [
                    SpreadsheetWorkbook(
                        path=workbook_path,
                        content_hash="workbook-hash",
                        source_path="sharepoint/contacts.xlsx",
                        aliases=("sharepoint/contacts.xlsx",),
                    )
                ],
            )
            matches = search_spreadsheet_rows(
                database, "workbook-hash", "WP1", "Who is Ada?", limit=5
            )

        self.assertEqual([sheet.name for sheet in sheets], ["WP1", "WP2"])
        self.assertEqual(sheets[0].headers, ("Name", "Institution"))
        self.assertEqual(matches[0].row_number, 2)
        self.assertEqual(matches[0].values["Name"], "Ada Lovelace")

    def test_sheet_query_without_row_terms_returns_bounded_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook_path = root / "contacts.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "WP2"
            worksheet.append(["Name"])
            worksheet.append(["Ada"])
            worksheet.append(["Grace"])
            workbook.save(workbook_path)
            database = root / "spreadsheets.duckdb"
            rebuild_spreadsheet_index(
                database,
                [
                    SpreadsheetWorkbook(
                        path=workbook_path,
                        content_hash="hash",
                        source_path="contacts.xlsx",
                        aliases=("contacts.xlsx",),
                    )
                ],
            )

            matches = search_spreadsheet_rows(database, "hash", "WP2", "Who is in WP2?", limit=1)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].row_number, 2)


class IndexStateTests(unittest.TestCase):
    def test_last_successful_index_state_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = write_index_state(directory, "OneDrive_1_11-13-2026", 12, 4, 3)
            persisted = read_index_state(directory)

        self.assertEqual(persisted, state)
        self.assertEqual(state["export"], "OneDrive_1_11-13-2026")
        self.assertEqual(state["documents"], 12)
        self.assertIn("+00:00", state["indexed_at"])


if __name__ == "__main__":
    unittest.main()

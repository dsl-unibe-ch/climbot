from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import load_workbook

_SEARCH_STOP_WORDS = frozenset(
    {"about", "are", "for", "from", "how", "in", "is", "of", "show", "the", "what", "who"}
)


@dataclass(frozen=True)
class SpreadsheetWorkbook:
    path: Path
    content_hash: str
    source_path: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class SpreadsheetSheet:
    workbook_id: str
    source: str
    source_path: str
    aliases: tuple[str, ...]
    name: str
    headers: tuple[str, ...]
    row_count: int

    @property
    def content(self) -> str:
        columns = ", ".join(self.headers) if self.headers else "No named columns"
        return (
            f"Spreadsheet {self.source}. Worksheet {self.name}. "
            f"Columns: {columns}. Data rows: {self.row_count}."
        )


@dataclass(frozen=True)
class SpreadsheetMatch:
    row_number: int
    values: dict[str, Any]


def spreadsheet_db_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / ".index" / "spreadsheets.duckdb"


def rebuild_spreadsheet_index(
    db_path: Path, workbooks: list[SpreadsheetWorkbook]
) -> list[SpreadsheetSheet]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("DROP TABLE IF EXISTS spreadsheet_rows")
        connection.execute(
            """
            CREATE TABLE spreadsheet_rows (
                workbook_id VARCHAR NOT NULL,
                workbook_path VARCHAR NOT NULL,
                sheet_name VARCHAR NOT NULL,
                row_number INTEGER NOT NULL,
                row_text VARCHAR NOT NULL,
                row_json VARCHAR NOT NULL
            )
            """
        )

        sheets: list[SpreadsheetSheet] = []
        for workbook in workbooks:
            sheets.extend(_index_workbook(connection, workbook))
        return sheets
    finally:
        connection.close()


def search_spreadsheet_rows(
    db_path: Path,
    workbook_id: str,
    sheet_name: str,
    query: str,
    limit: int = 10,
) -> list[SpreadsheetMatch]:
    if not db_path.exists():
        return []

    tokens = [
        token
        for token in dict.fromkeys(re.findall(r"[\w@.+-]{2,}", query.casefold()))
        if token not in _SEARCH_STOP_WORDS
    ][:8]
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = _find_matching_rows(connection, workbook_id, sheet_name, tokens)
        if not rows:
            rows = connection.execute(
                """
                SELECT row_number, row_text, row_json
                FROM spreadsheet_rows
                WHERE workbook_id = ? AND sheet_name = ?
                LIMIT ?
                """,
                [workbook_id, sheet_name, limit],
            ).fetchall()
    finally:
        connection.close()

    ranked = sorted(
        rows,
        key=lambda row: sum(token in row[1].casefold() for token in tokens),
        reverse=True,
    )
    return [
        SpreadsheetMatch(row_number=row_number, values=json.loads(row_json))
        for row_number, _, row_json in ranked[:limit]
    ]


def _find_matching_rows(
    connection: duckdb.DuckDBPyConnection,
    workbook_id: str,
    sheet_name: str,
    tokens: list[str],
) -> list[tuple[int, str, str]]:
    if not tokens:
        return []
    predicates = " OR ".join("contains(lower(row_text), ?)" for _ in tokens)
    return connection.execute(
        f"""
        SELECT row_number, row_text, row_json
        FROM spreadsheet_rows
        WHERE workbook_id = ? AND sheet_name = ? AND ({predicates})
        LIMIT 50
        """,
        [workbook_id, sheet_name, *tokens],
    ).fetchall()


def _index_workbook(
    connection: duckdb.DuckDBPyConnection, workbook: SpreadsheetWorkbook
) -> list[SpreadsheetSheet]:
    document = load_workbook(workbook.path, read_only=True, data_only=True)
    try:
        sheets: list[SpreadsheetSheet] = []
        for worksheet in document.worksheets:
            rows = worksheet.iter_rows(values_only=True)
            header_row_number, headers = _find_headers(rows)
            if not headers:
                sheets.append(
                    SpreadsheetSheet(
                        workbook_id=workbook.content_hash,
                        source=workbook.path.name,
                        source_path=workbook.source_path,
                        aliases=workbook.aliases,
                        name=worksheet.title,
                        headers=(),
                        row_count=0,
                    )
                )
                continue

            pending: list[tuple[str, str, str, int, str, str]] = []
            row_count = 0
            for row_number, values in enumerate(rows, start=header_row_number + 1):
                record = {
                    header: _json_value(value)
                    for header, value in zip(headers, values, strict=False)
                    if value is not None and str(value).strip()
                }
                if not record:
                    continue
                row_count += 1
                row_text = " | ".join(f"{key}: {value}" for key, value in record.items())
                pending.append(
                    (
                        workbook.content_hash,
                        workbook.source_path,
                        worksheet.title,
                        row_number,
                        row_text,
                        json.dumps(record, ensure_ascii=False),
                    )
                )
                if len(pending) == 500:
                    _insert_rows(connection, pending)
                    pending.clear()
            if pending:
                _insert_rows(connection, pending)

            sheets.append(
                SpreadsheetSheet(
                    workbook_id=workbook.content_hash,
                    source=workbook.path.name,
                    source_path=workbook.source_path,
                    aliases=workbook.aliases,
                    name=worksheet.title,
                    headers=tuple(headers),
                    row_count=row_count,
                )
            )
        return sheets
    finally:
        document.close()


def _find_headers(rows: Any) -> tuple[int, list[str]]:
    for row_number, values in enumerate(rows, start=1):
        if not any(value is not None and str(value).strip() for value in values):
            continue
        headers = []
        seen: dict[str, int] = {}
        for index, value in enumerate(values, start=1):
            base = (
                str(value).strip()
                if value is not None and str(value).strip()
                else f"Column {index}"
            )
            seen[base] = seen.get(base, 0) + 1
            headers.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
        return row_number, headers
    return 0, []


def _json_value(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _insert_rows(
    connection: duckdb.DuckDBPyConnection,
    rows: list[tuple[str, str, str, int, str, str]],
) -> None:
    connection.executemany(
        "INSERT INTO spreadsheet_rows VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )

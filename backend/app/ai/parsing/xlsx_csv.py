"""Tabular data — Excel workbooks (``openpyxl``) and CSV (stdlib ``csv``), together because both
reduce to the same shape: rows of cells, rendered as tab-separated lines so the column alignment
that makes a rate table readable survives into the indexed text.

**One workbook, one section per sheet.** A rate schedule with a "Domestic" tab and an
"International" tab genuinely are two different sections of the same document — heading them by
sheet name is what lets a citation say which one a retrieved row came from, and there is no other
structural signal in a spreadsheet to use instead.

**Formulas are read as their cached values, not their formula strings.** ``data_only=True`` on
``openpyxl`` — indexing ``"=SUM(A1:A10)"`` instead of ``"1,240.00"`` would make numeric search
useless on exactly the documents (rate tables, budget sheets) most likely to be spreadsheets.
"""

from __future__ import annotations

import csv
import io
from typing import Sequence

from app.ai.core.types import DocumentSection, RawDocument
from app.ai.parsing.base import DEFAULT_MAX_BYTES, BaseDocumentParser
from app.ai.parsing.text import decode_best_effort

XLSX_NAME = "xlsx"
XLSX_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
})
XLSX_EXTENSIONS = frozenset({".xlsx"})

CSV_NAME = "csv"
CSV_MIME_TYPES = frozenset({"text/csv", "application/csv"})
CSV_EXTENSIONS = frozenset({".csv"})

# A sheet or CSV with more rows than this is almost certainly a raw data export rather than a
# knowledge document meant to be read; capped so one enormous export cannot stall ingestion or
# produce a chunk set no retrieval budget could ever hold.
MAX_ROWS_PER_SHEET = 5_000


class XlsxParser(BaseDocumentParser):
    """Excel workbooks, one section per sheet."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES, recorder=None) -> None:
        super().__init__(
            name=XLSX_NAME, mime_types=XLSX_MIME_TYPES, extensions=XLSX_EXTENSIONS,
            max_bytes=max_bytes, recorder=recorder,
        )

    def is_available(self) -> bool:
        try:
            import openpyxl  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        import openpyxl

        workbook = openpyxl.load_workbook(
            io.BytesIO(document.content), read_only=True, data_only=True
        )
        try:
            sections = tuple(_sheet_sections(workbook))
        finally:
            workbook.close()
        text = "\n\n".join(s.text for s in sections)
        return text, sections


class CsvParser(BaseDocumentParser):
    """CSV — one section, since there is no sheet concept to divide it by."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES, recorder=None) -> None:
        super().__init__(
            name=CSV_NAME, mime_types=CSV_MIME_TYPES, extensions=CSV_EXTENSIONS,
            max_bytes=max_bytes, recorder=recorder,
        )

    def is_available(self) -> bool:
        return True

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        raw = decode_best_effort(document.content)
        rows = list(csv.reader(io.StringIO(raw)))[:MAX_ROWS_PER_SHEET]
        text = _rows_to_text(rows)
        section = DocumentSection(text=text) if text.strip() else None
        return text, ((section,) if section else ())


def _sheet_sections(workbook) -> Sequence[DocumentSection]:  # noqa: ANN001
    sections: list[DocumentSection] = []
    for sheet in workbook.worksheets:
        rows = []
        for count, row in enumerate(sheet.iter_rows(values_only=True)):
            if count >= MAX_ROWS_PER_SHEET:
                break
            rows.append(row)
        body = _rows_to_text(rows)
        if not body.strip():
            continue
        sections.append(
            DocumentSection(text=f"{sheet.title}\n{body}", heading=sheet.title, level=1)
        )
    return sections


def _rows_to_text(rows: Sequence[Sequence[object]]) -> str:
    lines = []
    for row in rows:
        cells = [_cell_text(c) for c in row]
        if any(cells):
            lines.append("\t".join(cells))
    return "\n".join(lines)


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


__all__ = [
    "CSV_EXTENSIONS",
    "CSV_MIME_TYPES",
    "CSV_NAME",
    "MAX_ROWS_PER_SHEET",
    "XLSX_EXTENSIONS",
    "XLSX_MIME_TYPES",
    "XLSX_NAME",
    "CsvParser",
    "XlsxParser",
]

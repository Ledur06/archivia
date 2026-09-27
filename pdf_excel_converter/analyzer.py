from __future__ import annotations

import re
from datetime import date

from .models import ExtractedRow, ExtractionResult


CURRENCY_TOKENS = ["FCFA", "F CFA", "XOF", "XAF", "€", "$", "£"]

NUMBER_CHARS_RE = re.compile(r"^[+-]?[\d\s .,]+$")

DATE_PATTERNS = [
    re.compile(r"^(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})$"),
    re.compile(r"^(?P<d>\d{1,2})-(?P<m>\d{1,2})-(?P<y>\d{4})$"),
    re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})$"),
]
ISO_DATE_RE = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$")


def analyze(result: ExtractionResult, mode: str = "both") -> ExtractionResult:
    previous_header: list[str] | None = None

    for page in result.pages:
        cleaned_tables = []
        for table in page.tables:
            cleaned_rows = [
                ExtractedRow(
                    values=[normalize_spaces(cell) for cell in row.values],
                    source_line=normalize_spaces(row.source_line),
                    kind=row.kind,
                )
                for row in table.rows
                if any(normalize_spaces(cell) for cell in row.values)
            ]
            if len(cleaned_rows) >= 2:
                table.rows = cleaned_rows
                cleaned_tables.append(table)

        previous_header = dedupe_repeated_headers(cleaned_tables, previous_header)
        page.tables = cleaned_tables

        page.rows = [
            ExtractedRow(
                values=[normalize_spaces(cell) for cell in row.values],
                source_line=normalize_spaces(row.source_line),
                kind=row.kind,
            )
            for row in page.rows
            if any(normalize_spaces(cell) for cell in row.values)
        ]

        if mode == "tables":
            page.rows = [row for table in page.tables for row in table.rows]
            page.text = ""
        elif mode == "text":
            page.tables = []

        if not page.tables and not page.text.strip() and not page.rows:
            message = f"Page {page.page_number} vide ou non exploitable"
            already_on_page = any("vide ou non exploitable" in warning for warning in page.warnings)
            already_global = any(f"Page {page.page_number}" in warning and "vide ou non exploitable" in warning for warning in result.warnings)
            if not already_on_page:
                page.warnings.append(message)
            if not already_global:
                result.warnings.append(message)

        # Future extension: apply an AI-assisted cleanup step here.

    return result


def normalize_spaces(value: str) -> str:
    return " ".join(str(value).replace(" ", " ").split()).strip()


# ---------------------------------------------------------------------------
# Deduplication des en-tetes repetes (tableau qui continue d'une page a
# l'autre, ou plusieurs blocs sur la meme page issus d'un seul vrai
# tableau coupe par la mise en page).
# ---------------------------------------------------------------------------


def dedupe_repeated_headers(
    tables: list, previous_header: list[str] | None
) -> list[str] | None:
    for table in tables:
        if not table.rows:
            continue
        first_values = [value.strip().lower() for value in table.rows[0].values]

        if previous_header is not None and first_values == previous_header and len(table.rows) > 2:
            dropped = table.rows[0]
            table.rows = table.rows[1:]
            message = (
                "En-tete repete supprime (identique au tableau precedent) : "
                + " | ".join(part for part in dropped.values if part)
            )
            if message not in table.warnings:
                table.warnings.append(message)
            continue

        if table.has_header:
            previous_header = first_values
        else:
            previous_header = None

    return previous_header


# ---------------------------------------------------------------------------
# Classification de cellules (nombre / date / texte) pour un export Excel
# avec des types natifs plutot que du texte brut partout.
# ---------------------------------------------------------------------------


def classify_cell(value: str) -> dict:
    """Determine si une cellule est un nombre, une date ou du texte.

    Retourne un dict {"kind", "value", "number_format"} exploitable
    directement par l'export Excel. Ne modifie jamais la donnee source :
    c'est une classification en lecture seule appliquee au moment de
    l'export.
    """
    if not isinstance(value, str):
        return {"kind": "text", "value": value, "number_format": None}

    text = value.strip()
    if not text:
        return {"kind": "text", "value": value, "number_format": None}

    parsed_date = parse_date(text)
    if parsed_date is not None:
        return {"kind": "date", "value": parsed_date, "number_format": "DD/MM/YYYY"}

    is_percent = text.endswith("%")
    number = parse_number(text)
    if number is not None:
        if is_percent:
            number_format = "0.00%"
        elif float(number).is_integer():
            number_format = "#,##0"
        else:
            number_format = "#,##0.00"
        return {"kind": "number", "value": number, "number_format": number_format}

    return {"kind": "text", "value": value, "number_format": None}


def parse_date(text: str) -> date | None:
    match = ISO_DATE_RE.match(text)
    if match:
        try:
            return date(int(match["y"]), int(match["m"]), int(match["d"]))
        except ValueError:
            return None

    for pattern in DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        try:
            return date(int(match["y"]), int(match["m"]), int(match["d"]))
        except ValueError:
            return None

    return None


def parse_number(text: str) -> float | None:
    working = text.strip().replace(" ", " ")
    if not working:
        return None

    for token in CURRENCY_TOKENS:
        if token in working:
            working = working.replace(token, "").strip()

    percent = working.endswith("%")
    if percent:
        working = working[:-1].strip()

    if not working or not NUMBER_CHARS_RE.match(working):
        return None
    if not re.search(r"\d", working):
        return None

    has_comma = "," in working
    has_dot = "." in working

    if has_comma and has_dot:
        if working.rfind(",") > working.rfind("."):
            cleaned = working.replace(".", "").replace(" ", "").replace(",", ".")
        else:
            cleaned = working.replace(",", "").replace(" ", "")
    elif has_comma:
        parts = working.split(",")
        if len(parts) == 2 and 1 <= len(parts[1].strip()) <= 2:
            cleaned = working.replace(" ", "").replace(",", ".")
        else:
            cleaned = working.replace(",", "").replace(" ", "")
    else:
        cleaned = working.replace(" ", "")

    try:
        number = float(cleaned)
    except ValueError:
        return None

    return number / 100 if percent else number

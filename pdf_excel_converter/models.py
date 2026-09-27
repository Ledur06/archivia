from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ExtractedRow:
    values: list[str]
    source_line: str
    kind: str


@dataclass(slots=True)
class ExtractedTable:
    table_id: str
    page_number: int
    rows: list[ExtractedRow]
    confidence: float
    warnings: list[str] = field(default_factory=list)
    has_header: bool = True
    detection_method: str = "lines"

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def num_rows(self) -> int:
        return self.row_count

    @property
    def column_count(self) -> int:
        if not self.rows:
            return 0
        return max(len(row.values) for row in self.rows)

    @property
    def num_columns(self) -> int:
        return self.column_count


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str
    extraction_method: str = "native"
    rows: list[ExtractedRow] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConsolidatedGroup:
    """Regroupement de tableaux decide par la passe de reorganisation
    document-entier (--smart-merge) : soit la fusion de plusieurs tableaux
    qui continuent le meme tableau sur des pages consecutives, soit un
    simple regroupement thematique de tableaux de meme nature qui restent
    distincts. N'existe que si l'utilisateur active --smart-merge ; sinon
    cette liste reste vide et rien d'autre ne change."""

    title: str
    category: str
    merged: bool
    tables: list[ExtractedTable] = field(default_factory=list)
    source_table_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class ExtractionResult:
    source_path: Path
    pages: list[ExtractedPage]
    warnings: list[str] = field(default_factory=list)
    consolidated_groups: list[ConsolidatedGroup] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(len(page.rows) for page in self.pages)

    @property
    def total_tables(self) -> int:
        return sum(len(page.tables) for page in self.pages)

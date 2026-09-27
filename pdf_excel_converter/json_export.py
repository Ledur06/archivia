from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import __version__
from .models import ConsolidatedGroup, ExtractedPage, ExtractedRow, ExtractedTable, ExtractionResult


def export_to_json(result: ExtractionResult, output_dir: Path, mode: str = "both") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{result.source_path.stem}_extraction.json"

    payload = asdict(result)
    payload["source_path"] = str(result.source_path)
    payload["exported_at"] = datetime.now().isoformat(timespec="seconds")
    payload["pipeline_version"] = __version__
    payload["mode"] = mode
    payload["summary"] = {
        "page_count": len(result.pages),
        "table_count": result.total_tables,
        "row_count": result.total_rows,
        "warning_count": len(result.warnings),
    }

    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return write_json_safely(output_path, content)


def write_json_safely(output_path: Path, content: str) -> Path:
    try:
        output_path.write_text(content, encoding="utf-8")
        return output_path
    except OSError:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback_path = output_path.with_name(f"{output_path.stem}_{timestamp}{output_path.suffix}")
        fallback_path.write_text(content, encoding="utf-8")
        return fallback_path


def row_from_dict(data: dict) -> ExtractedRow:
    return ExtractedRow(
        values=list(data.get("values", [])),
        source_line=data.get("source_line", ""),
        kind=data.get("kind", "text"),
    )


def table_from_dict(data: dict) -> ExtractedTable:
    return ExtractedTable(
        table_id=data["table_id"],
        page_number=data["page_number"],
        rows=[row_from_dict(row) for row in data.get("rows", [])],
        confidence=data.get("confidence", 0.0),
        warnings=list(data.get("warnings", [])),
        has_header=data.get("has_header", True),
        detection_method=data.get("detection_method", "lines"),
    )


def page_from_dict(data: dict) -> ExtractedPage:
    return ExtractedPage(
        page_number=data["page_number"],
        text=data.get("text", ""),
        extraction_method=data.get("extraction_method", "native"),
        rows=[row_from_dict(row) for row in data.get("rows", [])],
        tables=[table_from_dict(table) for table in data.get("tables", [])],
        warnings=list(data.get("warnings", [])),
    )


def consolidated_group_from_dict(data: dict) -> ConsolidatedGroup:
    return ConsolidatedGroup(
        title=data.get("title", ""),
        category=data.get("category", ""),
        merged=bool(data.get("merged", False)),
        tables=[table_from_dict(table) for table in data.get("tables", [])],
        source_table_ids=list(data.get("source_table_ids", [])),
        warnings=list(data.get("warnings", [])),
        reason=data.get("reason", ""),
    )


def extraction_result_from_dict(data: dict) -> ExtractionResult:
    try:
        pages = [page_from_dict(page) for page in data["pages"]]
        result = ExtractionResult(
            source_path=Path(data["source_path"]),
            pages=pages,
            warnings=list(data.get("warnings", [])),
            consolidated_groups=[
                consolidated_group_from_dict(group) for group in data.get("consolidated_groups", [])
            ],
        )
    except KeyError as exc:
        raise ValueError(
            f"JSON invalide ou incomplet pour recharger une extraction : champ manquant {exc}."
        ) from exc
    return result


def import_from_json(json_path: Path) -> tuple[ExtractionResult, str | None]:
    """Recharge un ExtractionResult depuis un JSON produit par export_to_json.

    Retourne le resultat et le mode ('tables'/'text'/'both') utilise lors de
    l'export d'origine, ou None si le JSON est anterieur a l'ajout du champ.
    """
    try:
        raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Le fichier {json_path} n'est pas un JSON valide : {exc}") from exc

    result = extraction_result_from_dict(raw)
    saved_mode = raw.get("mode")
    return result, saved_mode

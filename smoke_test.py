from __future__ import annotations

import argparse
from pathlib import Path

from pdf_excel_converter.analyzer import analyze
from pdf_excel_converter.export_excel import export_to_excel
from pdf_excel_converter.json_export import export_to_json
from pdf_excel_converter.models import ExtractedPage, ExtractedRow, ExtractionResult
from pdf_excel_converter.pdf_extract import detect_tables, extract_pdf


SAMPLE_PDF = Path("tests/sample.pdf")
OUTPUT_DIR = Path("outputs/excel")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test ArchivIA")
    parser.add_argument("--demo", action="store_true", help="Genere un Excel de demonstration sans PDF reel")
    args = parser.parse_args()

    if args.demo:
        return demo_without_pdf()

    create_sample_pdf(SAMPLE_PDF)
    result = analyze(extract_pdf(SAMPLE_PDF), mode="both")

    assert len(result.pages) == 2, f"Attendu 2 pages, obtenu {len(result.pages)}"
    assert result.total_tables >= 1, "Aucun tableau detecte dans le PDF echantillon"
    assert result.pages[0].tables, "Aucun tableau detecte sur la page 1"
    assert result.pages[0].tables[0].num_rows >= 3, "Le tableau detecte contient moins de 3 lignes"

    output_path = export_to_excel(result, OUTPUT_DIR)
    json_path = export_to_json(result, OUTPUT_DIR)
    assert output_path.exists(), f"Excel non genere: {output_path}"
    assert json_path.exists(), f"JSON non genere: {json_path}"

    print("SMOKE TEST OK")
    return 0


def create_sample_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError as exc:
        raise RuntimeError("reportlab est requis pour creer tests/sample.pdf") from exc

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    data = [
        ["Nom", "Age", "Ville"],
        ["Alice", "30", "Paris"],
        ["Bruno", "41", "Lyon"],
        ["Carla", "25", "Douala"],
        ["David", "38", "Marseille"],
    ]
    table = Table(data, colWidths=[160, 80, 160])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (1, 1), (1, -1), "CENTER"),
            ]
        )
    )
    story = [
        Paragraph("Tableau de test", styles["Title"]),
        table,
        PageBreak(),
        Paragraph("Texte brut de test", styles["Title"]),
        Paragraph(
            "Cette deuxieme page contient un paragraphe simple sans tableau. "
            "Elle sert a verifier que le texte brut reste disponible.",
            styles["BodyText"],
        ),
    ]
    doc.build(story)


def demo_without_pdf() -> int:
    rows = [
        ExtractedRow(["Nom", "Reference", "Montant"], "Nom | Reference | Montant", "table"),
        ExtractedRow(["Client A", "INV-001", "150000"], "Client A | INV-001 | 150000", "table"),
        ExtractedRow(["Client B", "INV-002", "275000"], "Client B | INV-002 | 275000", "table"),
        ExtractedRow(["Note a verifier dans le document source"], "Note a verifier", "text"),
    ]
    page = ExtractedPage(
        page_number=1,
        text="Demo",
        rows=rows,
        tables=detect_tables(rows, page_number=1),
        warnings=["Ligne de texte non tabulaire marquee pour verification."],
    )
    result = ExtractionResult(
        source_path=Path("demo.pdf"),
        pages=[page],
        warnings=["Demo: ligne de texte non tabulaire marquee pour verification."],
    )
    output_path = export_to_excel(result, OUTPUT_DIR)
    json_path = export_to_json(result, OUTPUT_DIR)
    print(f"Demo Excel OK: {output_path}")
    print(f"Demo JSON OK: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib.util
from pathlib import Path

from .portable_paths import find_poppler_bin, find_tesseract


def has_python_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def format_diagnostics() -> str:
    lines = ["Diagnostic environnement PDF vers Excel:"]
    lines.extend(format_python_checks())
    lines.extend(format_ocr_checks())
    return "\n".join(lines)


def format_python_checks() -> list[str]:
    checks = [
        ("pypdf", "Lecture PDF native"),
        ("pdfplumber", "Extraction geometrique des tableaux"),
        ("openpyxl", "Generation Excel"),
        ("pdf2image", "Conversion PDF vers image"),
        ("Pillow", "Manipulation image"),
        ("pytesseract", "Interface Python vers Tesseract"),
    ]
    lines: list[str] = []
    for module_name, role in checks:
        import_name = "PIL" if module_name == "Pillow" else module_name
        status = "OK" if has_python_module(import_name) else "MANQUANT"
        lines.append(f"[PY]  {module_name:<18}: {status} - {role}")
    return lines


def format_ocr_checks() -> list[str]:
    lines: list[str] = []

    try:
        import pytesseract
    except ImportError as exc:
        lines.append(f"[OCR] pytesseract       : MANQUANT - {type(exc).__name__}: {exc}")
        lines.append("[OCR] Tesseract         : NON TESTE - pytesseract indisponible")
    else:
        lines.append("[OCR] pytesseract       : OK")
        tesseract_path = find_tesseract()
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        searched_path = tesseract_path or "PATH Windows/Linux/Mac ou portable/Tesseract-OCR/tesseract.exe"
        try:
            version = pytesseract.get_tesseract_version()
            binary = pytesseract.pytesseract.tesseract_cmd
            lines.append(f"[OCR] Tesseract         : OK - version {version} - {binary}")
        except pytesseract.TesseractNotFoundError as exc:
            lines.append(f"[OCR] Tesseract         : INTROUVABLE - chemin cherche: {searched_path}")
            lines.append(f"[OCR] Tesseract erreur  : {type(exc).__name__}: {exc}")
        except Exception as exc:
            lines.append(f"[OCR] Tesseract         : ERREUR - chemin cherche: {searched_path}")
            lines.append(f"[OCR] Tesseract erreur  : {type(exc).__name__}: {exc}")

    try:
        from pdf2image import convert_from_path  # noqa: F401
    except ImportError as exc:
        lines.append(f"[OCR] pdf2image         : MANQUANT - {type(exc).__name__}: {exc}")
        lines.append("[OCR] Poppler (pdftoppm): NON TESTE - pdf2image indisponible")
    else:
        lines.append("[OCR] pdf2image         : OK")
        poppler_bin = find_poppler_bin()
        if poppler_bin:
            pdftoppm = Path(poppler_bin) / ("pdftoppm.exe" if Path(poppler_bin).drive else "pdftoppm")
            lines.append(f"[OCR] Poppler (pdftoppm): OK - {pdftoppm}")
        else:
            lines.append("[OCR] Poppler (pdftoppm): ABSENT - pdftoppm introuvable dans PATH ni dans portable/")

    return lines

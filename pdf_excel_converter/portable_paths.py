from __future__ import annotations

import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_tesseract() -> str | None:
    candidates = [
        PROJECT_ROOT / "portable" / "tesseract" / "tesseract.exe",
        PROJECT_ROOT / "portable" / "Tesseract-OCR" / "tesseract.exe",
        PROJECT_ROOT / "tools" / "tesseract" / "tesseract.exe",
        PROJECT_ROOT / "tools" / "Tesseract-OCR" / "tesseract.exe",
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]
    return find_executable("tesseract", candidates)


def find_poppler_bin() -> str | None:
    candidates = [
        PROJECT_ROOT / "portable" / "poppler" / "bin" / "pdftoppm.exe",
        PROJECT_ROOT / "portable" / "poppler" / "Library" / "bin" / "pdftoppm.exe",
        PROJECT_ROOT / "tools" / "poppler" / "bin" / "pdftoppm.exe",
        PROJECT_ROOT / "tools" / "poppler" / "Library" / "bin" / "pdftoppm.exe",
        Path("C:/poppler/Library/bin/pdftoppm.exe"),
        Path("C:/poppler/bin/pdftoppm.exe"),
        Path("C:/poppler-26.02.0/Library/bin/pdftoppm.exe"),
    ]
    found = find_executable("pdftoppm", candidates)
    return str(Path(found).parent) if found else None


def find_executable(command: str, candidates: list[Path]) -> str | None:
    path_result = shutil.which(command)
    if path_result:
        return path_result
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None

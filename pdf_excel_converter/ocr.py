from __future__ import annotations

from pathlib import Path

from .portable_paths import find_poppler_bin, find_tesseract


def _configure_tesseract():
    import pytesseract

    tesseract_cmd = find_tesseract()
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    return pytesseract


def _render_page_image(pdf_path: Path, page_number: int, dpi: int = 300):
    from pdf2image import convert_from_path

    poppler_path = find_poppler_bin()
    images = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        first_page=page_number,
        last_page=page_number,
        poppler_path=poppler_path,
    )
    return images[0] if images else None


def _preprocess_image(image):
    """Ameliore la lisibilite d'un scan de mauvaise qualite avant OCR :
    passage en niveaux de gris puis renforcement du contraste. Reste
    conservateur (pas de binarisation forcee) pour ne pas degrader les
    scans deja propres."""
    try:
        from PIL import ImageOps

        gray = image.convert("L")
        return ImageOps.autocontrast(gray, cutoff=1)
    except Exception:
        return image


def ocr_page(pdf_path: Path, page_number: int, lang: str = "fra+eng", dpi: int = 300) -> tuple[str, list[str]]:
    warnings: list[str] = []

    try:
        pytesseract = _configure_tesseract()
    except ImportError:
        return "", ["Module OCR manquant - lancez : pip install pytesseract pdf2image"]

    try:
        image = _render_page_image(pdf_path, page_number, dpi=dpi)
    except ImportError:
        return "", ["Module OCR manquant - lancez : pip install pytesseract pdf2image"]
    except Exception as exc:
        warnings.append(f"OCR echoue page {page_number} : {type(exc).__name__} - {exc}")
        return "", warnings

    if image is None:
        warnings.append(f"OCR vide page {page_number} - aucune image generee.")
        return "", warnings

    image = _preprocess_image(image)

    try:
        text = pytesseract.image_to_string(image, lang=lang)
    except pytesseract.TesseractNotFoundError:
        warnings.append(
            "OCR impossible : Tesseract introuvable. "
            "Installez Tesseract et relancez avec --diagnose pour verifier."
        )
        return "", warnings
    except Exception as exc:
        warnings.append(f"OCR echoue page {page_number} : {type(exc).__name__} - {exc}")
        return "", warnings

    if not text.strip():
        warnings.append(f"OCR vide page {page_number} - page peut-etre blanche ou image non reconnue.")

    return text, warnings


def ocr_word_boxes(
    pdf_path: Path, page_number: int, lang: str = "fra+eng", dpi: int = 300, min_confidence: int = 40
) -> list[dict]:
    """Renvoie les mots reconnus par Tesseract avec leur position en points
    PDF (pas en pixels), pour permettre la meme reconstruction de tableau
    par position que sur le texte natif.

    Chaque element : {"text", "left", "top", "width", "height"} en points,
    rapportes a la taille de la page (independant du DPI utilise pour le
    rendu de l'image OCR).
    """
    pytesseract = _configure_tesseract()
    from pytesseract import Output

    image = _render_page_image(pdf_path, page_number, dpi=dpi)
    if image is None:
        return []

    processed = _preprocess_image(image)
    data = pytesseract.image_to_data(processed, lang=lang, output_type=Output.DICT)

    # pdf2image rend a `dpi` points-par-pouce alors qu'une page PDF est en
    # points (72 par pouce) : on remet les coordonnees a l'echelle de la
    # page pour rester coherent avec les positions issues de pdfplumber.
    scale = 72.0 / dpi

    boxes: list[dict] = []
    for index, text in enumerate(data.get("text", [])):
        text = text.strip()
        if not text:
            continue
        try:
            confidence = int(float(data["conf"][index]))
        except (ValueError, KeyError):
            confidence = -1
        if confidence < min_confidence:
            continue
        boxes.append(
            {
                "text": text,
                "left": data["left"][index] * scale,
                "top": data["top"][index] * scale,
                "width": data["width"][index] * scale,
                "height": data["height"][index] * scale,
            }
        )
    return boxes

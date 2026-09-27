from __future__ import annotations

"""Structuration systematique des pages PDF par l'API Claude (vision).

Principe : plutot que de s'appuyer sur des heuristiques locales fragiles
(bordures pdfplumber, regex, position de mots, grille OpenCV...) pour
deviner la structure d'un tableau, on envoie directement l'IMAGE de la page
- native ou scannee, peu importe - a Claude, qui la lit comme le ferait un
humain et renvoie une structure JSON stricte (tableaux + texte). Ceci
fonctionne de la meme maniere quel que soit le type de document, ce qui
est le but recherche : ne plus dependre de la qualite du scan ou de la
complexite de la mise en page.

Le pipeline local (pdf_extract.py, ocr.py, table_vision.py) reste en place
comme filet de secours : si l'appel API echoue (reseau, cle invalide, quota,
reponse malformee) pour une page donnee, on retombe sur l'extraction locale
pour cette page plutot que de faire echouer tout le traitement.
"""

import base64
import json
import os
import re
from pathlib import Path

from .models import ExtractedPage, ExtractedRow, ExtractedTable


DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Tarifs indicatifs (USD / million de tokens), a jour a la redaction de ce
# module - Anthropic peut les faire evoluer. Utilises uniquement pour
# afficher une estimation de cout apres traitement, jamais pour facturer :
# la facturation reelle vient du compte Anthropic de l'utilisateur.
MODEL_PRICING_PER_MTOK = {
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
}

STRUCTURING_PROMPT = """Tu vas recevoir l'image d'une page de document (PDF natif ou scanne). Lis-la attentivement et renvoie UNIQUEMENT un objet JSON (aucun texte avant ou apres, aucun bloc markdown ```), avec exactement cette forme :

{
  "tables": [
    {
      "has_header": true,
      "headers": ["Colonne 1", "Colonne 2"],
      "rows": [["valeur", "valeur"], ["valeur", "valeur"]]
    }
  ],
  "text_blocks": ["paragraphe de texte libre 1", "paragraphe de texte libre 2"]
}

Regles importantes :
- Un tableau = donnees organisees en lignes/colonnes avec un sens tabulaire clair (bordures visibles, ou alignement clair en colonnes). Une liste a puces ou un titre ne sont PAS des tableaux.
- Si une cellule est fusionnee sur plusieurs lignes ou colonnes dans le document source, repete sa valeur sur toutes les lignes/colonnes qu'elle couvre (pour que le tableau reste exploitable tel quel dans un tableur).
- Respecte fidelement le texte source (orthographe, accents, casse, nombres). Ne traduis rien, ne resume rien, ne corrige pas les fautes du document original.
- "has_header": true seulement si la premiere ligne du tableau est clairement un intitule de colonnes (pas une donnee).
- "text_blocks" contient le texte libre de la page qui n'appartient a aucun tableau, regroupe par paragraphe (pas ligne par ligne).
- Si la page est vide, illisible ou ne contient rien d'exploitable, renvoie {"tables": [], "text_blocks": []}.
- Le JSON doit etre strictement valide (guillemets doubles, pas de virgule finale)."""


def get_api_key() -> str | None:
    """Lit la cle API depuis la variable d'environnement ANTHROPIC_API_KEY.

    Jamais stockee en dur dans le code ni dans un fichier versionne : c'est
    a l'utilisateur de la definir sur sa machine (voir README)."""
    return os.environ.get("ANTHROPIC_API_KEY") or None


def render_page_png_bytes(pdf_path: Path, page_number: int, dpi: int = 150) -> bytes | None:
    """Rasterise une page PDF (native ou scannee - peu importe, c'est le but)
    en image PNG, pour l'envoyer telle quelle a Claude. Reutilise le meme
    rendu que le pipeline OCR local (poppler via pdf2image) pour rester
    coherent et ne pas ajouter de dependance supplementaire."""
    from .ocr import _render_page_image

    image = _render_page_image(pdf_path, page_number, dpi=dpi)
    if image is None:
        return None

    import io

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def structure_page_with_claude(
    pdf_path: Path,
    page_number: int,
    api_key: str,
    model: str = DEFAULT_MODEL,
    dpi: int = 150,
    max_retries: int = 1,
) -> tuple[ExtractedPage | None, list[str], dict | None]:
    """Envoie l'image d'une page a Claude et construit un ExtractedPage a
    partir de sa reponse structuree.

    Retourne (page_ou_None, avertissements, usage_tokens_ou_None). page est
    None si l'appel API a definitivement echoue (reseau, cle invalide,
    reponse invalide meme apres relance) : l'appelant doit alors se rabattre
    sur l'extraction locale pour cette page. usage_tokens permet d'agreger
    une estimation de cout sur l'ensemble du document.
    """
    warnings: list[str] = []

    try:
        import anthropic
    except ImportError:
        return None, ["Module 'anthropic' non installe - lancez : pip install anthropic"], None

    image_bytes = render_page_png_bytes(pdf_path, page_number, dpi=dpi)
    if image_bytes is None:
        return None, [f"Rendu image impossible page {page_number} pour envoi a Claude."], None

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as exc:
        return None, [f"Client Anthropic non initialisable : {type(exc).__name__} - {exc}"], None

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": STRUCTURING_PROMPT},
                        ],
                    }
                ],
            )
        except Exception as exc:
            # On attrape toute exception (pas seulement anthropic.APIError) :
            # une erreur reseau, un proxy mal configure, un certificat invalide
            # etc. ne doivent jamais faire planter tout le traitement du
            # document - seulement declencher le repli sur l'extraction
            # locale pour cette page.
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        try:
            raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
                "model": model,
            }

            parsed = parse_claude_json(raw_text)
            if parsed is None:
                last_error = "Reponse Claude non-JSON ou malformee."
                continue

            page = build_extracted_page(parsed, page_number)
        except Exception as exc:
            # Reponse recue mais structure inattendue (schema different de
            # ce qui est demande) : on retente plutot que de faire echouer
            # tout le document.
            last_error = f"Reponse Claude exploitee incorrectement : {type(exc).__name__} - {exc}"
            continue

        return page, warnings, usage

    warnings.append(f"Appel Claude echoue page {page_number} apres {max_retries + 1} tentative(s) : {last_error}")
    return None, warnings, None


def parse_claude_json(raw_text: str) -> dict | None:
    """Extrait et valide le JSON renvoye par Claude, en tolerant les cas
    frequents ou le modele entoure malgre tout sa reponse de texte ou de
    balises markdown, meme quand on le lui a explicitement interdit."""
    text = raw_text.strip()

    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    if not text.startswith("{"):
        brace_index = text.find("{")
        if brace_index == -1:
            return None
        text = text[brace_index:]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Dernier recours : parfois du texte suit le JSON valide (Claude
        # ajoute un commentaire malgre la consigne) - on tente de ne garder
        # que jusqu'a la derniere accolade fermante correspondante.
        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(text)
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict) or "tables" not in data:
        return None
    return data


def build_extracted_page(parsed: dict, page_number: int) -> ExtractedPage:
    tables: list[ExtractedTable] = []

    for index, raw_table in enumerate(parsed.get("tables") or [], start=1):
        headers = [str(h) for h in (raw_table.get("headers") or [])]
        raw_rows = raw_table.get("rows") or []
        has_header = bool(raw_table.get("has_header", True)) and bool(headers)

        rows: list[ExtractedRow] = []
        if has_header:
            rows.append(ExtractedRow(values=list(headers), source_line=" | ".join(headers), kind="table"))
        for raw_row in raw_rows:
            values = [str(v) if v is not None else "" for v in raw_row]
            rows.append(ExtractedRow(values=values, source_line=" | ".join(values), kind="table"))

        if len(rows) < (2 if has_header else 1):
            continue

        max_cols = max(len(row.values) for row in rows)
        normalized = [
            ExtractedRow(
                values=row.values + [""] * (max_cols - len(row.values)),
                source_line=row.source_line,
                kind=row.kind,
            )
            for row in rows
        ]

        tables.append(
            ExtractedTable(
                table_id=f"page_{page_number}_claude_{index}",
                page_number=page_number,
                rows=normalized,
                confidence=0.97,
                warnings=[],
                has_header=has_header,
                detection_method="claude",
            )
        )

    text_blocks = [str(t) for t in (parsed.get("text_blocks") or []) if str(t).strip()]
    rows_from_text = [
        ExtractedRow(values=[block], source_line=block, kind="text") for block in text_blocks
    ]

    combined_text = "\n\n".join(text_blocks)

    return ExtractedPage(
        page_number=page_number,
        text=combined_text,
        extraction_method="claude",
        rows=rows_from_text,
        tables=tables,
        warnings=[],
    )


def estimate_cost_usd(total_usage: list[dict]) -> float:
    """Estimation indicative du cout total, a partir des tarifs connus au
    moment de l'ecriture de ce module. A titre informatif seulement - la
    facture reelle vient du compte Anthropic."""
    total = 0.0
    for usage in total_usage:
        pricing = MODEL_PRICING_PER_MTOK.get(usage["model"])
        if not pricing:
            continue
        total += usage["input_tokens"] / 1_000_000 * pricing["input"]
        total += usage["output_tokens"] / 1_000_000 * pricing["output"]
    return round(total, 4)

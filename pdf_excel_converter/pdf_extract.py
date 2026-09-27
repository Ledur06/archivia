from __future__ import annotations

import re
from pathlib import Path

from .models import ExtractedPage, ExtractedRow, ExtractedTable, ExtractionResult
from .ocr import ocr_page


TABLE_SPLIT_RE = re.compile(r"\s{2,}")
BULLET_OR_LIST_RE = re.compile(r"^([a-zA-Z]\)|\d+[\.)]|[-•−])$")
SHORT_CODE_RE = re.compile(r"^[A-Z]{2,4}$")
NUMERIC_LIKE_RE = re.compile(r"^[+-]?[\d\s.,]+%?$")

# Sous ce score de qualite (rempli/rows), on tente un niveau de detection
# supplementaire plutot que de garder un tableau probablement mal reconstruit.
MIN_ACCEPTABLE_TABLE_QUALITY = 0.35
# Au-dela de ce score, un resultat est deja tres bon : pas la peine de payer
# le cout du niveau de detection par position des mots.
NEAR_PERFECT_TABLE_QUALITY = 0.95


def extract_pdf(
    pdf_path: Path,
    enable_ocr: bool = False,
    max_pages: int | None = None,
    ocr_lang: str = "fra+eng",
    pages_selection: str | None = None,
    mode: str = "both",
    ocr_dpi: int = 300,
) -> ExtractionResult:
    try:
        import pdfplumber
    except ImportError:
        warning = (
            "pdfplumber non installe - detection de tableaux degradee. "
            "Lancez : pip install pdfplumber"
        )
        return extract_pdf_with_pypdf(
            pdf_path=pdf_path,
            enable_ocr=enable_ocr,
            max_pages=max_pages,
            ocr_lang=ocr_lang,
            pages_selection=pages_selection,
            mode=mode,
            ocr_dpi=ocr_dpi,
            initial_warnings=[warning],
        )

    pages: list[ExtractedPage] = []
    warnings: list[str] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        selected_pages = resolve_pages_to_process(
            total_pages=len(pdf.pages),
            pages_selection=pages_selection,
            max_pages=max_pages,
        )

        for page_number in selected_pages:
            page = pdf.pages[page_number - 1]
            page_warnings: list[str] = []
            # layout=True preserve les grands espaces entre colonnes ; le
            # texte "aplati" par defaut de pdfplumber les compresse en un
            # seul espace et rend toute detection de tableau par texte
            # impossible (colonnes fusionnees en une seule chaine).
            native_text = page.extract_text(layout=True) or ""

            tables: list[ExtractedTable] = []
            if mode != "text":
                tables, table_warnings = detect_page_tables(page, page_number, native_text)
                page_warnings.extend(table_warnings)

            text = native_text
            method = "native" if text.strip() or tables else "none"

            is_scanned = page_is_scanned_image(page)
            if enable_ocr and looks_unreliable(text, tables, is_scanned_image=is_scanned):
                # On tente d'abord la grille de lignes reelle (une seule
                # passe OCR sur la page). Si elle reussit, on en deduit
                # aussi le texte de la page a partir de son contenu, ce qui
                # evite un DEUXIEME appel OCR complet (ocr_page) rien que
                # pour le texte brut - un appel OCR pleine page coute deja
                # plusieurs secondes, le doubler inutilement ralentit
                # sensiblement le traitement d'un document scanne.
                grid_table = None
                grid_warnings: list[str] = []
                if mode != "text":
                    grid_table, grid_warnings = try_ocr_grid_table(pdf_path, page_number, ocr_dpi, ocr_lang)
                page_warnings.extend(grid_warnings)

                if grid_table is not None:
                    tables = [grid_table]
                    text = "\n".join(
                        " | ".join(value for value in row.values if value) for row in grid_table.rows
                    )
                    method = "ocr"
                else:
                    ocr_text, ocr_warnings = ocr_page(pdf_path, page_number, lang=ocr_lang, dpi=ocr_dpi)
                    page_warnings.extend(ocr_warnings)
                    if ocr_text.strip():
                        text = ocr_text
                        method = "ocr"
                        if mode != "text":
                            ocr_tables, ocr_table_warnings = extract_tables_from_ocr(
                                pdf_path, page_number, ocr_text, ocr_dpi=ocr_dpi, ocr_lang=ocr_lang, skip_grid=True
                            )
                            page_warnings.extend(ocr_table_warnings)
                            # Sur une page qui est fondamentalement une image
                            # scannee, un "tableau" trouve dans le texte
                            # natif est par construction suspect (au mieux
                            # une couche d'OCR integree de qualite inconnue) :
                            # on privilegie l'OCR frais des qu'il produit
                            # quelque chose, plutot que de departager par un
                            # score structurel qui peut etre trompeur (du
                            # texte errone mais bien aligne obtient un score
                            # eleve).
                            if ocr_tables and (is_scanned or table_set_quality(ocr_tables) > table_set_quality(tables)):
                                tables = ocr_tables

            tables = [finalize_table(table) for table in tables]

            page_rows = build_page_rows(text, tables, mode)
            stored_text = "" if mode == "tables" else text
            extracted_page = ExtractedPage(
                page_number=page_number,
                text=stored_text,
                extraction_method=method,
                rows=page_rows,
                tables=[] if mode == "text" else tables,
                warnings=page_warnings,
            )
            add_page_quality_warnings(extracted_page, warnings, mode)
            pages.append(extracted_page)

    return ExtractionResult(source_path=pdf_path, pages=pages, warnings=warnings)


def extract_pdf_with_pypdf(
    pdf_path: Path,
    enable_ocr: bool = False,
    max_pages: int | None = None,
    ocr_lang: str = "fra+eng",
    pages_selection: str | None = None,
    mode: str = "both",
    ocr_dpi: int = 300,
    initial_warnings: list[str] | None = None,
) -> ExtractionResult:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Les dependances pdfplumber et pypdf sont manquantes. "
            "Installez requirements.txt puis relancez."
        ) from exc

    reader = PdfReader(str(pdf_path))
    pages: list[ExtractedPage] = []
    warnings: list[str] = list(initial_warnings or [])

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover - depends on PDF encryption
            raise RuntimeError("PDF chiffre impossible a ouvrir sans mot de passe.") from exc

    selected_pages = resolve_pages_to_process(
        total_pages=len(reader.pages),
        pages_selection=pages_selection,
        max_pages=max_pages,
    )

    for page_number in selected_pages:
        page = reader.pages[page_number - 1]
        text = extract_page_text_with_pypdf(page)
        method = "native" if text.strip() else "none"
        page_warnings: list[str] = []

        tables = [] if mode == "text" else detect_tables(extract_rows_from_text(text), page_number)

        if enable_ocr and looks_unreliable(text, tables):
            ocr_text, ocr_warnings = ocr_page(pdf_path, page_number, lang=ocr_lang, dpi=ocr_dpi)
            page_warnings.extend(ocr_warnings)
            if ocr_text.strip():
                text = ocr_text
                method = "ocr"
                if mode != "text":
                    ocr_tables, ocr_table_warnings = extract_tables_from_ocr(
                        pdf_path, page_number, ocr_text, ocr_dpi=ocr_dpi
                    )
                    page_warnings.extend(ocr_table_warnings)
                    if table_set_quality(ocr_tables) > table_set_quality(tables):
                        tables = ocr_tables

        tables = [finalize_table(table) for table in tables]

        text_rows = [] if mode == "tables" else extract_rows_from_text(text)
        page_rows = build_page_rows(text, tables, mode, fallback_rows=text_rows)

        extracted_page = ExtractedPage(
            page_number=page_number,
            text="" if mode == "tables" else text,
            extraction_method=method,
            rows=page_rows,
            tables=tables,
            warnings=page_warnings,
        )
        add_page_quality_warnings(extracted_page, warnings, mode)
        pages.append(extracted_page)

    return ExtractionResult(source_path=pdf_path, pages=pages, warnings=warnings)


def extract_pdf_with_claude(
    pdf_path: Path,
    api_key: str,
    model: str,
    max_pages: int | None = None,
    pages_selection: str | None = None,
    mode: str = "both",
    claude_dpi: int = 150,
    local_fallback_ocr: bool = False,
    ocr_lang: str = "fra+eng",
    ocr_dpi: int = 300,
) -> tuple[ExtractionResult, list[dict]]:
    """Structure systematiquement chaque page d'un PDF via l'API Claude
    (voir claude_structuring.py), peu importe qu'il s'agisse d'un PDF natif
    ou d'un scan - c'est le principe demande : ne plus dependre de la
    qualite/complexite du document, Claude lit directement l'image de
    chaque page.

    Si l'appel Claude echoue pour une page precise (reseau, quota, reponse
    invalide meme apres relance), on se rabat sur l'extraction locale pour
    CETTE page uniquement, avec un avertissement explicite - le traitement
    du reste du document continue normalement plutot que d'echouer en bloc.

    Retourne (resultat, liste des usages tokens par page reussie) - cette
    derniere sert a estimer le cout total en fin de traitement.
    """
    from .claude_structuring import structure_page_with_claude

    total_pages = count_pdf_pages(pdf_path)
    selected_pages = resolve_pages_to_process(
        total_pages=total_pages,
        pages_selection=pages_selection,
        max_pages=max_pages,
    )

    pages: list[ExtractedPage] = []
    warnings: list[str] = []
    usage_log: list[dict] = []

    for page_number in selected_pages:
        page, page_warnings, usage = structure_page_with_claude(
            pdf_path, page_number, api_key, model=model, dpi=claude_dpi
        )

        if page is None:
            local_result = extract_pdf(
                pdf_path,
                enable_ocr=local_fallback_ocr,
                pages_selection=str(page_number),
                mode=mode,
                ocr_lang=ocr_lang,
                ocr_dpi=ocr_dpi,
            )
            page = local_result.pages[0] if local_result.pages else ExtractedPage(
                page_number=page_number, text="", extraction_method="none"
            )
            fallback_message = "Repli sur extraction locale : " + "; ".join(page_warnings)
            page.warnings.append(fallback_message)
            warnings.append(f"Page {page_number}: {fallback_message}")
        else:
            usage_log.append(usage)
            if mode == "tables":
                page.rows = [row for table in page.tables for row in table.rows]
                page.text = ""
            elif mode == "text":
                page.tables = []
            page.warnings.extend(page_warnings)
            warnings.extend(f"Page {page_number}: {w}" for w in page_warnings)

        pages.append(page)

    return ExtractionResult(source_path=pdf_path, pages=pages, warnings=warnings), usage_log


def count_pdf_pages(pdf_path: Path) -> int:
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    except ImportError:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)


def resolve_pages_to_process(
    total_pages: int,
    pages_selection: str | None = None,
    max_pages: int | None = None,
) -> list[int]:
    if pages_selection:
        return parse_page_selection(pages_selection, total_pages)
    if max_pages:
        return list(range(1, min(max_pages, total_pages) + 1))
    return list(range(1, total_pages + 1))


def parse_page_selection(pages_str: str, total_pages: int) -> list[int]:
    selected: set[int] = set()
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(part))
    return sorted(page for page in selected if 1 <= page <= total_pages)


# ---------------------------------------------------------------------------
# Detection de tableaux a plusieurs niveaux (pdfplumber uniquement)
#
# 1. Bordures reelles (page.extract_tables(), rapide, fiable si le PDF a un
#    vrai quadrillage).
# 2. Alignement par texte a espacement preserve (regex sur les grands
#    espaces de native_text, rapide).
# 3. Position reelle des mots (page.extract_words(), plus lent mais robuste
#    aux mises en page ou l'espacement du texte est irregulier).
#
# On ne descend au niveau suivant que si le resultat precedent est vide ou
# de mauvaise qualite (beaucoup de cellules vides / peu de lignes), pour
# eviter de payer le cout de extract_words() sur des pages deja bien
# detectees, et pour eviter qu'une strategie plus agressive n'ecrase un bon
# resultat plus simple.
# ---------------------------------------------------------------------------


def detect_page_tables(page, page_number: int, native_text: str) -> tuple[list[ExtractedTable], list[str]]:
    warnings: list[str] = []

    lines_tables, lines_warnings = extract_tables_with_pdfplumber(page, page_number)
    warnings.extend(lines_warnings)
    regex_tables = detect_tables(extract_rows_from_text(native_text), page_number)

    lines_quality = table_set_quality(lines_tables)
    regex_quality = table_set_quality(regex_tables)

    if regex_quality > lines_quality:
        chosen, quality = regex_tables, regex_quality
    else:
        chosen, quality = lines_tables, lines_quality

    # On ne tente le niveau 3 (plus lent : extract_words() sur toute la
    # page) que si au moins un des deux niveaux rapides a deja trouve
    # quelque chose - un signe qu'il y a probablement un vrai tableau ici -
    # mais que le resultat n'est pas encore excellent. Sur une page sans
    # aucun contenu tabulaire (prose pure), les deux niveaux rapides
    # renvoient 0 tableau et on ne paie pas le cout du niveau 3 : c'est
    # une limite assumee pour garder des temps de traitement raisonnables
    # sur les PDF de plusieurs centaines de pages.
    found_something = bool(lines_tables or regex_tables)
    if found_something and quality < NEAR_PERFECT_TABLE_QUALITY:
        word_tables, word_warnings = extract_tables_by_word_position(page, page_number)
        warnings.extend(word_warnings)
        word_quality = table_set_quality(word_tables)
        if word_quality > quality:
            chosen, quality = word_tables, word_quality

    return chosen, warnings


def table_set_quality(tables: list[ExtractedTable]) -> float:
    if not tables:
        return 0.0
    all_rows = [row for table in tables for row in table.rows]
    fill = fill_ratio(all_rows)
    return fill * min(1.0, len(all_rows) / 4)


def fill_ratio(rows: list[ExtractedRow]) -> float:
    total = 0
    filled = 0
    for row in rows:
        for value in row.values:
            total += 1
            if value.strip():
                filled += 1
    return filled / total if total else 0.0


def extract_tables_with_pdfplumber(page, page_number: int) -> tuple[list[ExtractedTable], list[str]]:
    tables: list[ExtractedTable] = []
    warnings: list[str] = []
    try:
        raw_tables = page.extract_tables() or []
    except Exception as exc:
        return [], [f"Extraction tableaux pdfplumber echouee page {page_number}: {type(exc).__name__} - {exc}"]

    for table_index, raw_table in enumerate(raw_tables, start=1):
        rows: list[ExtractedRow] = []
        for raw_row in raw_table:
            cells = [normalize_cell(cell) for cell in raw_row]
            rows.append(ExtractedRow(values=cells, source_line=" | ".join(cells), kind="table"))
        rows = [row for row in rows if any(cell.strip() for cell in row.values)]
        if len(rows) < 2:
            continue
        max_cols = max(len(row.values) for row in rows)
        if max_cols < 2:
            # Une seule colonne detectee par les bordures : c'est plus
            # souvent une table des matieres ou une liste a puces (lignes
            # separees par des filets) qu'un vrai tableau. On la laisse
            # au texte brut plutot que de fabriquer un tableau a 1 colonne.
            continue
        normalized_rows = normalize_table_width(rows)
        quality = fill_ratio(normalized_rows)
        confidence = round(min(0.98, 0.6 + quality * 0.35 + min(len(normalized_rows), 10) * 0.01), 2)
        tables.append(
            ExtractedTable(
                table_id=f"p{page_number}_t{table_index}",
                page_number=page_number,
                rows=normalized_rows,
                confidence=confidence,
                warnings=[],
                detection_method="lines",
            )
        )

    return tables, warnings


# ---------------------------------------------------------------------------
# Niveau 3 : reconstruction par position reelle des mots.
# ---------------------------------------------------------------------------


def extract_tables_by_word_position(
    page, page_number: int, min_gap: float = 8.0, y_tolerance: float = 3.0
) -> tuple[list[ExtractedTable], list[str]]:
    try:
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    except Exception as exc:
        return [], [
            f"Detection par position des mots echouee page {page_number}: {type(exc).__name__} - {exc}"
        ]

    if not words:
        return [], []

    lines = build_word_lines(words, y_tolerance=y_tolerance)
    line_groups = [(line, group_words_by_gap(line["words"], min_gap=min_gap)) for line in lines]

    tables: list[ExtractedTable] = []
    current_block: list[tuple[dict, list[list[dict]]]] = []
    for line, groups in line_groups:
        if len(groups) >= 2:
            current_block.append((line, groups))
        else:
            flush_word_block(current_block, tables, page_number, min_gap)
            current_block = []
    flush_word_block(current_block, tables, page_number, min_gap)

    return tables, []


def build_word_lines(words: list[dict], y_tolerance: float = 3.0) -> list[dict]:
    lines: list[dict] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        target = None
        for line in lines:
            if abs(line["top"] - word["top"]) <= y_tolerance:
                target = line
                break
        if target is None:
            lines.append({"top": word["top"], "words": [word]})
        else:
            target["words"].append(word)
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
    lines.sort(key=lambda line: line["top"])
    return lines


def group_words_by_gap(words: list[dict], min_gap: float = 8.0) -> list[list[dict]]:
    if not words:
        return []
    groups = [[words[0]]]
    for word in words[1:]:
        previous = groups[-1][-1]
        if word["x0"] - previous["x1"] >= min_gap:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def flush_word_block(
    block: list[tuple[dict, list[list[dict]]]],
    tables: list[ExtractedTable],
    page_number: int,
    min_gap: float,
) -> None:
    if len(block) < 2:
        return

    starts = sorted(group[0]["x0"] for _line, groups in block for group in groups)
    columns = cluster_positions(starts, gap_threshold=min_gap * 1.5)
    if len(columns) < 2:
        return

    rows: list[ExtractedRow] = []
    for _line, groups in block:
        values = ["" for _ in columns]
        for group in groups:
            text = " ".join(word["text"] for word in group)
            group_x0 = group[0]["x0"]
            idx = min(range(len(columns)), key=lambda i: abs(columns[i] - group_x0))
            values[idx] = (values[idx] + " " + text).strip() if values[idx] else text
        source_line = " | ".join(value for value in values if value)
        rows.append(ExtractedRow(values=values, source_line=source_line, kind="table"))

    if len(rows) < 2:
        return
    if looks_like_bulleted_list(rows):
        return

    quality = fill_ratio(rows)
    if quality < MIN_ACCEPTABLE_TABLE_QUALITY:
        return

    confidence = round(min(0.95, 0.5 + quality * 0.35 + min(len(rows), 10) * 0.01), 2)
    warnings = ["Tableau reconstruit par position des mots (pas de bordures ni d'espacement regulier)."]

    tables.append(
        ExtractedTable(
            table_id=f"page_{page_number}_wtable_{len(tables) + 1}",
            page_number=page_number,
            rows=rows,
            confidence=confidence,
            warnings=warnings,
            detection_method="position",
        )
    )


def cluster_positions(values: list[float], gap_threshold: float) -> list[float]:
    if not values:
        return []
    clusters: list[list[float]] = [[values[0]]]
    for value in values[1:]:
        if value - clusters[-1][-1] <= gap_threshold:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


# ---------------------------------------------------------------------------
# OCR : declenchement, et reconstruction de tableau depuis les positions
# de mots renvoyees par pytesseract.
# ---------------------------------------------------------------------------


def page_is_scanned_image(page) -> bool:
    """Detecte si une page est fondamentalement une image scannee/photographiee
    (une grande image recouvre l'essentiel de la page), par opposition a une
    page de texte natif veritable.

    Sert a ne pas se laisser tromper par du texte "natif" qui serait en
    realite une couche d'OCR de mauvaise qualite integree par le scanner ou
    le photocopieur lui-meme : structurellement, ce texte peut ressembler a
    un tableau bien forme tout en etant illisible sur le fond. Si la page
    est essentiellement une image, on ne fait jamais confiance a son texte
    "natif" pour decider si l'OCR est necessaire.
    """
    try:
        images = page.images
    except Exception:
        return False
    if not images:
        return False
    try:
        page_area = float(page.width) * float(page.height)
    except Exception:
        return False
    if page_area <= 0:
        return False
    for image in images:
        try:
            width = float(image.get("x1", 0)) - float(image.get("x0", 0))
            height = float(image.get("bottom", 0)) - float(image.get("top", 0))
        except (TypeError, ValueError):
            continue
        if width * height >= 0.5 * page_area:
            return True
    return False


def looks_unreliable(text: str, tables: list[ExtractedTable], is_scanned_image: bool = False) -> bool:
    """Decide si l'OCR doit prendre le relais du texte natif.

    Declenche quand il n'y a ni texte ni tableau (page scannee "pure"),
    quand la page est fondamentalement une image scannee/photographiee (voir
    page_is_scanned_image - dans ce cas le texte "natif" eventuel n'est
    jamais considere fiable, meme s'il a permis de detecter un "tableau"),
    quand le texte natif semble etre du bruit (tres peu de caracteres
    alphanumeriques), ou quand des tableaux ont ete trouves mais que leur
    qualite structurelle n'est pas quasi parfaite.
    """
    stripped = text.strip()
    if not stripped and not tables:
        return True
    if is_scanned_image:
        return True
    if tables:
        return table_set_quality(tables) < NEAR_PERFECT_TABLE_QUALITY
    if len(stripped) < 20:
        return True
    alnum = sum(1 for char in stripped if char.isalnum())
    ratio = alnum / len(stripped)
    return ratio < 0.35


def extract_tables_from_ocr(
    pdf_path: Path,
    page_number: int,
    ocr_text: str,
    ocr_dpi: int = 300,
    ocr_lang: str = "fra+eng",
    skip_grid: bool = False,
) -> tuple[list[ExtractedTable], list[str]]:
    """Reconstruit les tableaux d'une page scannee, du plus fiable au moins
    fiable :

    1. Grille de lignes reelle de l'image (table_vision.py) - le cas le plus
       courant pour un document administratif scanne (tableau a bordures).
       Beaucoup plus robuste qu'un regroupement par position de mots car il
       s'appuie sur les vraies bordures dessinees, pas sur des espaces OCR
       qui peuvent etre deformes par une erreur de lecture. Ignore si
       skip_grid=True (deja tente par l'appelant avant de lancer l'OCR
       texte brut - evite de retenter une operation couteuse).
    2. Position des mots OCR (pytesseract image_to_data) - pour les tableaux
       sans bordures visibles mais a colonnes bien alignees.
    3. Detection par texte simple (regex) - dernier recours.
    """
    warnings: list[str] = []

    if not skip_grid:
        grid_table, grid_warnings = try_ocr_grid_table(pdf_path, page_number, ocr_dpi, ocr_lang)
        warnings.extend(grid_warnings)
        if grid_table is not None:
            return [grid_table], warnings

    try:
        from .ocr import ocr_word_boxes

        boxes = ocr_word_boxes(pdf_path, page_number, dpi=ocr_dpi)
    except Exception as exc:  # pragma: no cover - depend de l'environnement OCR
        boxes = []
        warnings.append(f"Positions OCR indisponibles page {page_number}: {type(exc).__name__} - {exc}")

    if boxes:
        lines = build_word_lines(
            [{"text": b["text"], "x0": b["left"], "x1": b["left"] + b["width"], "top": b["top"]} for b in boxes],
            y_tolerance=5.0,
        )
        line_groups = [(line, group_words_by_gap(line["words"], min_gap=15.0)) for line in lines]
        tables: list[ExtractedTable] = []
        current_block: list[tuple[dict, list[list[dict]]]] = []
        for line, groups in line_groups:
            if len(groups) >= 2:
                current_block.append((line, groups))
            else:
                flush_word_block(current_block, tables, page_number, 15.0)
                current_block = []
        flush_word_block(current_block, tables, page_number, 15.0)
        if tables:
            return tables, warnings

    return detect_tables(extract_rows_from_text(ocr_text), page_number), warnings


def try_ocr_grid_table(
    pdf_path: Path, page_number: int, ocr_dpi: int, ocr_lang: str
) -> tuple[ExtractedTable | None, list[str]]:
    """Tente la reconstruction par grille de lignes reelle (voir
    table_vision.py). Echoue silencieusement (sans avertissement) si
    opencv/numpy ne sont pas installes - c'est une amelioration optionnelle,
    le pipeline reste fonctionnel sans elle via les niveaux suivants."""
    try:
        from .ocr import _render_page_image
        from .table_vision import ocr_grid_table
    except ImportError:
        return None, []

    try:
        image = _render_page_image(pdf_path, page_number, dpi=ocr_dpi)
    except ImportError:
        return None, []
    except Exception as exc:
        return None, [f"Rendu image page {page_number} impossible pour detection de grille: {type(exc).__name__} - {exc}"]

    if image is None:
        return None, []

    try:
        grid, grid_warnings = ocr_grid_table(image, lang=ocr_lang)
    except ImportError:
        return None, []
    except Exception as exc:
        return None, [f"Detection de grille (tableau scanne) echouee page {page_number}: {type(exc).__name__} - {exc}"]

    if grid is None:
        return None, grid_warnings

    rows = [
        ExtractedRow(values=list(row), source_line=" | ".join(v for v in row if v), kind="table") for row in grid
    ]
    rows = [row for row in rows if any(cell.strip() for cell in row.values)]
    if len(rows) < 2:
        return None, grid_warnings

    quality = fill_ratio(rows)
    if quality < MIN_ACCEPTABLE_TABLE_QUALITY:
        return None, grid_warnings

    confidence = round(min(0.95, 0.55 + quality * 0.35 + min(len(rows), 10) * 0.01), 2)
    table = ExtractedTable(
        table_id=f"page_{page_number}_gridtable_1",
        page_number=page_number,
        rows=normalize_table_width(rows),
        confidence=confidence,
        warnings=list(grid_warnings),
        detection_method="grid",
    )
    return table, grid_warnings


# ---------------------------------------------------------------------------
# Repli texte (pypdf, ou tableau introuvable) : heuristique par regex.
# ---------------------------------------------------------------------------


def build_page_rows(
    text: str,
    tables: list[ExtractedTable],
    mode: str,
    fallback_rows: list[ExtractedRow] | None = None,
) -> list[ExtractedRow]:
    if mode == "tables":
        return [row for table in tables for row in table.rows]
    if mode == "text":
        return group_into_paragraphs(extract_text_rows(text))
    if fallback_rows is not None:
        return group_into_paragraphs(fallback_rows)
    return group_into_paragraphs(extract_rows_from_text(text))


def extract_text_rows(text: str) -> list[ExtractedRow]:
    rows: list[ExtractedRow] = []
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if line:
            rows.append(ExtractedRow(values=[line], source_line=line, kind="text"))
    return rows


def group_into_paragraphs(rows: list[ExtractedRow]) -> list[ExtractedRow]:
    """Regroupe les lignes de texte consecutives (kind="text") en paragraphes,
    pour un rendu en "zone de texte" dans l'Excel plutot qu'un empilement
    d'une ligne source par ligne de tableau. Les lignes deja identifiees
    comme appartenant a un tableau (kind="table") ne sont jamais fusionnees :
    elles restent telles quelles pour ne pas alterer une structure deja
    detectee."""
    grouped: list[ExtractedRow] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraph = " ".join(buffer)
            grouped.append(ExtractedRow(values=[paragraph], source_line=paragraph, kind="text"))
            buffer.clear()

    for row in rows:
        if row.kind == "text" and row.values and row.values[0].strip():
            buffer.append(row.values[0].strip())
        else:
            flush()
            grouped.append(row)

    flush()
    return grouped


def add_page_quality_warnings(page: ExtractedPage, global_warnings: list[str], mode: str) -> None:
    if not page.text.strip() and not page.tables:
        message = f"Page {page.page_number} vide ou non exploitable."
        page.warnings.append(message)
        global_warnings.append(message)
        return
    if mode != "text" and not page.tables:
        message = "Texte detecte, mais aucun tableau structure n'a ete isole."
        page.warnings.append(message)
        global_warnings.append(f"Page {page.page_number}: {message}")


def extract_page_text_with_pypdf(page) -> str:
    try:
        return page.extract_text(extraction_mode="layout") or ""
    except TypeError:
        return page.extract_text() or ""


def extract_rows_from_text(text: str) -> list[ExtractedRow]:
    rows: list[ExtractedRow] = []

    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if not line:
            continue

        values = split_table_like_line(line)
        if is_table_like_values(values):
            rows.append(ExtractedRow(values=values, source_line=line, kind="table"))
        else:
            rows.append(ExtractedRow(values=[line], source_line=line, kind="text"))

    return rows


def detect_tables(rows: list[ExtractedRow], page_number: int) -> list[ExtractedTable]:
    tables: list[ExtractedTable] = []
    current: list[ExtractedRow] = []

    for row in rows:
        if row.kind == "table":
            current.append(row)
            continue
        flush_table_candidate(current, tables, page_number)
        current = []

    flush_table_candidate(current, tables, page_number)
    return tables


def flush_table_candidate(
    candidate: list[ExtractedRow],
    tables: list[ExtractedTable],
    page_number: int,
) -> None:
    # Ce detecteur ne s'appuie que sur des espaces dans le texte, sans
    # aucune information geometrique : c'est le moins fiable des 3 niveaux.
    # A 2 lignes seulement, il produit trop souvent de faux positifs
    # (un titre et une ligne suivante qui partagent par coincidence le
    # meme nombre de "colonnes"). On exige donc au moins 3 lignes ici,
    # alors que les niveaux bases sur les bordures ou la position reelle
    # des mots restent acceptes des 2 lignes.
    if len(candidate) < 3:
        return

    column_counts = [len(row.values) for row in candidate]
    main_column_count = max(set(column_counts), key=column_counts.count)
    if main_column_count < 2:
        return

    consistent_rows = sum(1 for count in column_counts if abs(count - main_column_count) <= 1)
    consistency = consistent_rows / len(candidate)
    if consistency < 0.5:
        return

    if looks_like_bulleted_list(candidate):
        return

    confidence = round(min(0.99, 0.55 + (consistency * 0.35) + min(len(candidate), 10) * 0.01), 2)

    warnings: list[str] = []
    if consistency < 0.8:
        warnings.append("Nombre de colonnes variable; verifier la structure du tableau.")

    table_id = f"page_{page_number}_table_{len(tables) + 1}"
    tables.append(
        ExtractedTable(
            table_id=table_id,
            page_number=page_number,
            rows=normalize_table_width(candidate),
            confidence=confidence,
            warnings=warnings,
            detection_method="regex",
        )
    )


def normalize_table_width(rows: list[ExtractedRow]) -> list[ExtractedRow]:
    max_cols = max(len(row.values) for row in rows)
    normalized: list[ExtractedRow] = []
    for row in rows:
        values = row.values + [""] * (max_cols - len(row.values))
        normalized.append(ExtractedRow(values=values, source_line=row.source_line, kind=row.kind))
    return normalized


def normalize_cell(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace(" ", " ").split()).strip()


def normalize_line(line: str) -> str:
    return line.replace(" ", " ").strip()


def split_table_like_line(line: str) -> list[str]:
    if "\t" in line:
        return clean_values(line.split("\t"))
    if "|" in line:
        return clean_values(line.split("|"))
    if ";" in line:
        return clean_values(line.split(";"))

    return clean_values(TABLE_SPLIT_RE.split(line))


def clean_values(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]


def is_table_like_values(values: list[str]) -> bool:
    if len(values) < 2:
        return False
    if len(values) >= 3:
        return True

    first, second = values[0], values[1]
    if BULLET_OR_LIST_RE.match(first.strip()):
        return False
    if len(second) > 90 and count_numeric_cells(values) == 0:
        return False
    if len(first) <= 4 and len(second) > 50:
        return False
    return count_numeric_cells(values) > 0 or len(first) <= 45


def count_numeric_cells(values: list[str]) -> int:
    return sum(1 for value in values if re.search(r"\d", value))


def looks_like_bulleted_list(rows: list[ExtractedRow]) -> bool:
    first_cells = [row.values[0].strip() for row in rows if row.values]
    if not first_cells:
        return False
    bullet_count = sum(1 for value in first_cells if BULLET_OR_LIST_RE.match(value))
    return bullet_count / len(first_cells) >= 0.5


# ---------------------------------------------------------------------------
# Post-traitement des tableaux : en-tetes multi-lignes, detection d'absence
# d'en-tete.
# ---------------------------------------------------------------------------


def finalize_table(table: ExtractedTable) -> ExtractedTable:
    flag_possible_multiline_header(table)
    table.has_header = row_looks_like_header(table.rows[0], table.rows[1:]) if table.rows else True
    return table


def flag_possible_multiline_header(table: ExtractedTable) -> None:
    """Signale (sans modifier les donnees) que les 2 premieres lignes
    pourraient former un en-tete reparti sur 2 lignes.

    Fusionner automatiquement est risque : sur des donnees imparfaitement
    colonnees, deux LIGNES DE DONNEES distinctes peuvent presenter le meme
    motif "complementaire" par coincidence (une colonne mal detectee sur
    l'une des deux). On se contente donc d'avertir pour verification
    manuelle, sans jamais fusionner ni perdre d'information.
    """
    if len(table.rows) < 3:
        return
    row0, row1 = table.rows[0], table.rows[1]
    if len(row0.values) != len(row1.values) or len(row0.values) < 2:
        return
    if any(re.search(r"\d", value) for value in row0.values):
        return

    # Une ligne d'en-tete multi-lignes n'a normalement AUCUNE colonne ou
    # les deux lignes sont remplies en meme temps (sinon ce sont deux
    # lignes de donnees distinctes, pas un en-tete partage).
    both_filled = 0
    complementary = 0
    for a, b in zip(row0.values, row1.values):
        a, b = a.strip(), b.strip()
        if a and b:
            both_filled += 1
        elif bool(a) != bool(b):
            complementary += 1

    if both_filled > 0:
        return
    if complementary < len(row0.values) - 1:
        return

    message = (
        "Les 2 premieres lignes pourraient former un en-tete reparti sur 2 lignes "
        "- a verifier manuellement (non fusionne automatiquement)."
    )
    if message not in table.warnings:
        table.warnings.append(message)


def row_looks_like_header(row: ExtractedRow, data_rows: list[ExtractedRow]) -> bool:
    """Heuristique conservatrice : une ligne ou la MAJORITE des cellules
    ressemblent a un code court tout en majuscules (type code pays/devise)
    ou a une valeur purement numerique ressemble a de la donnee, pas a un
    intitule de colonne.

    On exige une majorite plutot qu'une seule cellule suspecte : un vrai
    intitule de colonne peut legitimement etre un sigle court (ex. "CRI",
    "REF", "N°") sans que la ligne entiere soit pour autant une ligne de
    donnees.
    """
    if not data_rows:
        return True
    non_empty = [value.strip() for value in row.values if value.strip()]
    if not non_empty:
        return True
    suspicious = sum(1 for value in non_empty if SHORT_CODE_RE.match(value) or NUMERIC_LIKE_RE.match(value))
    return suspicious / len(non_empty) < 0.5

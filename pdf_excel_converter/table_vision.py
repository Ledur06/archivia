from __future__ import annotations

"""Reconstruction de tableaux scannes a partir de la grille de lignes reelle
de l'image, plutot que d'un regroupement par position/espacement du texte
OCR.

Contexte : sur un PDF scanne contenant un vrai tableau a bordures (ce qui est
le cas le plus courant en administration : listes, correspondances,
nomenclatures), deviner les colonnes a partir des espaces entre mots OCR est
fragile - la moindre erreur de lecture ou le moindre desalignement casse la
structure. Cette approche s'appuie a la place sur les lignes horizontales et
verticales effectivement dessinees sur la page (detectees par traitement
d'image), ce qui est beaucoup plus proche de ce que fait un lecteur humain :
on regarde d'abord la grille, puis on lit le contenu de chaque case.

Cette methode gere nativement les cellules fusionnees (une case qui s'etend
sur plusieurs lignes/colonnes a simplement une boite englobante plus grande),
sans heuristique separee de detection de fusion.

Depend d'opencv-python (cv2) et numpy, absents par defaut de requirements.txt
de base des projets plus anciens : toutes les fonctions ici echouent
proprement avec ImportError si ces paquets ne sont pas installes, et le code
appelant (pdf_extract.py) se rabat alors sur la methode par position de mots.
"""


def deskew_image(pil_image):
    """Redresse une image legerement de travers (photo ou scan penche).

    Detecte l'angle dominant des longues lignes droites de la page (les
    bordures du tableau, tres majoritairement) via une transformee de Hough,
    puis fait pivoter l'image de l'angle oppose. Ne corrige pas la distorsion
    de perspective (page courbee/photographiee en angle) - seulement la
    rotation simple, qui est de loin le cas le plus frequent avec un
    scanner ou une photo de document a plat.

    Retourne (image_redressee, angle_degres). Si aucun angle net n'est
    detecte, retourne l'image d'origine et un angle de 0.0.
    """
    import cv2
    import numpy as np
    from PIL import Image

    gray = np.array(pil_image.convert("L"))
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    lines = cv2.HoughLinesP(
        binary, 1, np.pi / 360, threshold=200, minLineLength=gray.shape[1] // 4, maxLineGap=20
    )

    angles = []
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            if abs(angle) < 15:
                angles.append(angle)

    if not angles:
        return pil_image, 0.0

    angle = float(np.median(angles))
    if abs(angle) < 0.1:
        return pil_image, 0.0

    height, width = gray.shape
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        np.array(pil_image), matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return Image.fromarray(rotated), angle


def _cluster_1d(values: list[float], gap: float) -> list[float]:
    values = sorted(values)
    clusters = [[values[0]]]
    for value in values[1:]:
        if value - clusters[-1][-1] <= gap:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def detect_table_cells(pil_image) -> dict | None:
    """Detecte la grille d'un tableau a bordures dans une image de page.

    Principe : on isole les pixels appartenant a de longues lignes
    horizontales et verticales (par erosion/dilatation morphologique avec
    des noyaux tres allonges - un trait de texte, meme un "l" ou un accent,
    n'est jamais aussi long qu'une bordure de tableau). Les cases du tableau
    sont alors les composantes connexes du fond (l'inverse de ce masque de
    lignes) : une case normale est une petite region, une case fusionnee sur
    plusieurs lignes/colonnes est simplement une region plus grande - pas
    besoin de logique de fusion separee.

    Retourne un dict {"grid_rows", "grid_cols", "placements"} ou chaque
    placement est {"row_start","row_end","col_start","col_end","x","y","w","h"}
    (bornes en indices de grille, coordonnees en pixels pour le decoupage).
    Retourne None si aucune grille fiable n'est trouvee (page sans bordures,
    tableau non-quadrille, ou trop peu de cellules detectees) : le code
    appelant doit alors se rabattre sur une autre methode.
    """
    import cv2
    import numpy as np

    gray = np.array(pil_image.convert("L"))
    height, width = gray.shape
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)

    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(width // 20, 1), 1))
    horiz = cv2.dilate(cv2.erode(binary, horiz_kernel), horiz_kernel)
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(height // 35, 1)))
    vert = cv2.dilate(cv2.erode(binary, vert_kernel), vert_kernel)
    grid_mask = cv2.bitwise_or(horiz, vert)

    ys, xs = np.nonzero(grid_mask)
    if len(xs) == 0:
        return None
    table_x0, table_x1 = int(xs.min()), int(xs.max())
    table_y0, table_y1 = int(ys.min()), int(ys.max())
    table_area = (table_x1 - table_x0) * (table_y1 - table_y0)
    if table_area <= 0:
        return None

    inverted = cv2.bitwise_not(grid_mask)
    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(inverted, connectivity=4)

    cells: list[tuple[int, int, int, int]] = []
    for i in range(1, num_labels):
        x, y, cw, ch, area = stats[i]
        if area < 300:
            continue  # bruit
        if x <= 1 or y <= 1 or x + cw >= width - 1 or y + ch >= height - 1:
            continue  # composante qui touche le bord de la page : fond hors tableau
        if area > table_area * 0.35:
            continue  # trop grand pour etre une cellule
        cells.append((int(x), int(y), int(cw), int(ch)))

    if len(cells) < 6:
        return None

    row_gap = max(10, (table_y1 - table_y0) // 150)
    col_gap = max(10, (table_x1 - table_x0) // 60)
    row_edges = _cluster_1d([c[1] for c in cells] + [c[1] + c[3] for c in cells], gap=row_gap)
    col_edges = _cluster_1d([c[0] for c in cells] + [c[0] + c[2] for c in cells], gap=col_gap)

    grid_rows = len(row_edges) - 1
    grid_cols = len(col_edges) - 1
    if grid_rows < 2 or grid_cols < 2:
        return None

    def nearest_index(edges: list[float], value: float) -> int:
        return min(range(len(edges)), key=lambda i: abs(edges[i] - value))

    placements = []
    for (x, y, cw, ch) in cells:
        r0 = nearest_index(row_edges, y)
        r1 = max(nearest_index(row_edges, y + ch), r0 + 1)
        c0 = nearest_index(col_edges, x)
        c1 = max(nearest_index(col_edges, x + cw), c0 + 1)
        placements.append(
            {"row_start": r0, "row_end": r1, "col_start": c0, "col_end": c1, "x": x, "y": y, "w": cw, "h": ch}
        )

    return {"grid_rows": grid_rows, "grid_cols": grid_cols, "placements": placements}


def ocr_grid_table(pil_image, lang: str = "fra+eng") -> tuple[list[list[str]] | None, list[str]]:
    """Reconstruit un tableau a partir de la grille de lignes reelle de la
    page.

    Important pour la performance : on fait l'OCR en UNE seule passe sur
    toute l'image (pytesseract.image_to_data), puis on repartit chaque mot
    reconnu dans la cellule de grille a laquelle il appartient par position.
    Un appel Tesseract par cellule (une centaine sur un tableau administratif
    typique) a ete teste et rejete : chaque invocation a un cout de demarrage
    fixe d'environ une seconde, ce qui rend le traitement d'une seule page
    trop lent (plus d'une minute) pour un gain de precision qui ne le
    justifie pas.

    Le texte de chaque case fusionnee est reporte sur toutes les
    lignes/colonnes qu'elle couvre ("fill-down"), pour produire un tableau
    exploitable directement (filtrable, triable) au lieu de cases vides qui
    ne le seraient qu'a l'affichage.

    Retourne (grille, avertissements) ou (None, avertissements) si aucune
    grille fiable n'a ete trouvee.
    """
    import pytesseract
    from pytesseract import Output

    deskewed, _angle = deskew_image(pil_image)
    layout = detect_table_cells(deskewed)
    if layout is None:
        return None, []

    try:
        data = pytesseract.image_to_data(deskewed, lang=lang, output_type=Output.DICT)
    except Exception:
        return None, []

    words = []
    for i, text in enumerate(data.get("text", [])):
        text = text.strip()
        if not text:
            continue
        try:
            confidence = int(float(data["conf"][i]))
        except (ValueError, KeyError):
            confidence = -1
        if confidence < 0:
            continue
        words.append(
            {
                "text": text,
                "cx": data["left"][i] + data["width"][i] / 2,
                "cy": data["top"][i] + data["height"][i] / 2,
                "top": data["top"][i],
                "left": data["left"][i],
            }
        )

    grid: list[list[str]] = [["" for _ in range(layout["grid_cols"])] for _ in range(layout["grid_rows"])]
    warnings: list[str] = []
    oversized_merges = 0

    for placement in layout["placements"]:
        x0, y0 = placement["x"], placement["y"]
        x1, y1 = x0 + placement["w"], y0 + placement["h"]
        cell_words = [w for w in words if x0 - 2 <= w["cx"] <= x1 + 2 and y0 - 2 <= w["cy"] <= y1 + 2]
        cell_words.sort(key=lambda w: (w["top"], w["left"]))
        text = " ".join(w["text"] for w in cell_words)

        row_span = placement["row_end"] - placement["row_start"]
        col_span = placement["col_end"] - placement["col_start"]
        if row_span > 1 and col_span > 1:
            oversized_merges += 1

        for r in range(placement["row_start"], placement["row_end"]):
            for c in range(placement["col_start"], placement["col_end"]):
                grid[r][c] = text

    if oversized_merges:
        warnings.append(
            f"{oversized_merges} cellule(s) semblent fusionnees sur plusieurs lignes ET colonnes a la "
            "fois - a verifier, cela peut signaler deux colonnes reunies par erreur (bordure manquante "
            "sur l'image source)."
        )

    return grid, warnings

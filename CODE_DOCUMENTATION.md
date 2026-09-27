# ArchivIA - Documentation technique

Cette documentation decrit le role de chaque fichier et le flux d'execution actuel.

## Flux global

```text
Convert-PDFToExcel.ps1 / convert.sh / python -m pdf_excel_converter
  -> cli.py
  -> pdf_extract.py (extract_pdf, ou extract_pdf_with_claude si --use-claude)
  -> analyzer.py
  -> export_excel.py
  -> json_export.py
```

L'OCR local passe par `ocr.py` et `table_vision.py` uniquement si `--ocr` est active et si une page ne contient pas de texte/tableau natif exploitable (voir `looks_unreliable` / `page_is_scanned_image`).

La structuration par API passe par `claude_structuring.py` uniquement si `--use-claude` est active ; en cas d'echec pour une page, `extract_pdf_with_claude` se rabat sur `extract_pdf` pour cette page uniquement.

## Racine du projet

### Convert-PDFToExcel.ps1

Lanceur Windows PowerShell.

- Declare les parametres utilisateur : `PdfPath`, `OutputDir`, `Ocr`, `OcrLang`, `TesseractPath`, `PopplerBin`, `Pages`, `Mode`, `MaxPages`.
- Ne couvre pas encore `--use-claude`, `--from-json`, `--batch-dir`, `--strict` : utiliser `python -m pdf_excel_converter` directement pour ces options.
- Detecte le dossier du projet pour permettre un lancement depuis n'importe quel repertoire.
- Utilise le Python visible dans la session PowerShell avec `python -c "import sys; print(sys.executable)"`.
- Ajoute explicitement le site-packages utilisateur a `PYTHONPATH` quand Python ne le charge pas automatiquement.
- Ajoute Tesseract/Poppler au `PATH` de la session si les chemins sont fournis ou si les chemins Windows courants existent.
- Transmet les options a `python -m pdf_excel_converter`.
- Debloque les fichiers `.xlsx` generes pour limiter les problemes de mode protege Windows.

### convert.sh

Lanceur Linux/Mac.

- Accepte `--pdf`, `--ocr`, `--ocr-lang`, `--pages`, `--mode`, `--max-pages`, `--output-dir`.
- Se place automatiquement dans le dossier du script.
- Lance `python3 -m pdf_excel_converter`.
- Doit etre rendu executable sur Linux/Mac avec `chmod +x convert.sh`.

### smoke_test.py

Test de bout en bout.

- Cree `tests/sample.pdf` avec `reportlab`.
- Page 1 : tableau reel de 3 colonnes.
- Page 2 : texte brut sans tableau.
- Lance `extract_pdf()`, puis `analyze()`, puis les exports Excel/JSON.
- Verifie : 2 pages, au moins 1 tableau, au moins 3 lignes dans le premier tableau, fichiers crees.
- Option `--demo` : conserve l'ancien mode demonstration sans PDF reel.

### requirements.txt

Dependances Python :

- `pdfplumber` : extraction geometrique des tableaux (3 niveaux : bordures, alignement de texte, position des mots).
- `pypdf` : fallback texte si `pdfplumber` est absent.
- `openpyxl` : generation Excel (tableaux natifs, styles, hyperliens).
- `pdf2image` : conversion PDF vers image (OCR local et rendu pour Claude).
- `pillow` : support/pretraitement image.
- `pytesseract` : interface Python vers Tesseract.
- `opencv-python-headless` + `numpy` : detection de grille de tableau sur scan (optionnel - repli automatique sans eux).
- `anthropic` : appels a l'API Claude (utilise uniquement si `--use-claude`).

### pyproject.toml

Configuration package Python.

- Nom package : `pdf-excel-converter`.
- Script console : `pdf-excel-converter = pdf_excel_converter.cli:main`.
- Dependances identiques a `requirements.txt`.

## Package `pdf_excel_converter`

### __init__.py

Contient la version interne de l'outil : `__version__`.

### __main__.py

Permet l'execution :

```bash
python -m pdf_excel_converter
```

Il appelle `cli.main()`.

### cli.py

Point d'entree Python.

Arguments :

- `pdf_path` : fichier PDF source.
- `--output-dir` : dossier des sorties.
- `--diagnose` : affiche les dependances et l'etat OCR.
- `--ocr` : active OCR local.
- `--ocr-lang` : langues Tesseract, par exemple `fra+eng`.
- `--ocr-dpi` : resolution de rendu avant OCR local (defaut 300).
- `--max-pages` : traite les N premieres pages.
- `--pages` : selection precise, par exemple `1,3,5-7`.
- `--mode` : `tables`, `text` ou `both`.
- `--from-json` : recharge une extraction JSON deja faite, regenere seulement l'Excel.
- `--batch-dir` : traite tous les PDF d'un dossier a la suite.
- `--strict` : code de sortie 2 si `has_quality_issues()` detecte un probleme.
- `--use-claude` : structure chaque page via l'API Claude au lieu des heuristiques locales.
- `--claude-model` : modele Claude (defaut `claude-haiku-4-5-20251001`).
- `--claude-dpi` : resolution de l'image envoyee a Claude (defaut 150).
- `--smart-merge` : passe de reorganisation document-entier (fusion de tableaux multi-pages, regroupement thematique) via `claude_reorganization.py`.
- `--reorg-model` : modele Claude pour `--smart-merge` (defaut `claude-sonnet-5`).

Fonctions cles :

- `main()` : parse les arguments, dispatch vers `run_batch`, `run_from_json` ou le flux normal. Verifie que `ANTHROPIC_API_KEY` est definie si `--use-claude` ou `--smart-merge` est passe.
- `convert_one(pdf_path, output_dir, args, mode)` : orchestre une conversion complete pour un seul PDF. Appelle `extract_pdf_with_claude()` si `args.use_claude`, sinon `extract_pdf()`, puis `analyze()`. Si `args.smart_merge`, appelle `run_smart_merge()` avant l'export. Affiche ensuite le nombre de pages structurees par Claude et le cout estime (`estimate_cost_usd`) si applicable.
- `run_smart_merge(result, args)` : lance `reorganize_document_with_claude()` puis `apply_reorganization_plan()`, peuple `result.consolidated_groups`. Ne leve jamais - ajoute un avertissement et laisse `result` inchange en cas d'echec (cle manquante, appel API en erreur). Retourne la liste d'usage token pour le calcul de cout, utilisee par `convert_one()` et `run_from_json()`.
- `run_from_json()` : recharge un `ExtractionResult` via `import_from_json()`, applique `--smart-merge` si demande, puis regenere uniquement l'Excel.
- `run_batch()` : traite tous les `*.pdf` d'un dossier, continue sur erreur par fichier, agrege les codes de sortie.
- `has_quality_issues(result)` : vrai si des avertissements existent ou si un tableau a une confiance < 0.8 (utilise par `--strict`).
- `safe_print()` : evite les `UnicodeEncodeError` sur les consoles Windows en repli ASCII.

### models.py

Dataclasses centrales (`slots=True`).

`ExtractedRow`

- `values` : cellules de la ligne.
- `source_line` : ligne source lisible.
- `kind` : `table` ou `text`.

`ExtractedTable`

- `table_id` : identifiant stable.
- `page_number` : page source.
- `rows` : lignes de tableau.
- `confidence` : score de qualite structurelle (0 a 1).
- `warnings` : avertissements specifiques a ce tableau.
- `has_header` : si `rows[0]` est un intitule de colonnes ou deja une donnee.
- `detection_method` : `lines` (bordures pdfplumber), `regex` (alignement texte), `position` (position de mots), `grid` (grille de lignes sur image scannee), ou `claude` (structuration API).
- `row_count` / `num_rows`, `column_count` / `num_columns` : proprietes calculees.

`ExtractedPage`

- `page_number` : numero de page.
- `text` : texte brut stocke selon le mode.
- `extraction_method` : `native`, `ocr`, `claude` ou `none`.
- `rows` : lignes retenues (texte regroupe en paragraphes hors tableaux, voir `group_into_paragraphs`).
- `tables` : tableaux detectes.
- `warnings` : problemes de page.

`ConsolidatedGroup`

- `title` / `category` : titre et categorie du groupe, decides par `claude_reorganization.py`.
- `merged` : `True` si `tables` contient UN SEUL tableau resultant de la fusion de plusieurs tableaux multi-pages ; `False` si `tables` contient plusieurs tableaux distincts simplement regroupes.
- `tables` : tableau(x) final/finaux du groupe.
- `source_table_ids` : `table_id` d'origine ayant servi a construire le groupe (tracabilite).
- `warnings` : avertissements specifiques au groupe (ex: structures de colonnes differentes lors d'une fusion).
- `reason` : justification courte generee par Claude (pourquoi ces tableaux sont rapproches), affichee telle quelle dans la feuille `Tableaux_consolides`.

`ExtractionResult`

- `source_path` : PDF source.
- `pages` : pages traitees.
- `warnings` : avertissements globaux.
- `consolidated_groups` : liste de `ConsolidatedGroup`, peuplee uniquement si `--smart-merge` a reussi. Vide sinon - n'affecte jamais `pages`.
- `total_rows` et `total_tables` : statistiques (basees sur `pages`, pas sur `consolidated_groups`).

### portable_paths.py

Recherche les executables externes.

- `find_tesseract()` cherche dans `portable/`, `tools/`, `C:\Program Files\Tesseract-OCR`, puis PATH.
- `find_poppler_bin()` cherche `pdftoppm` dans `portable/`, `tools/`, `C:\poppler\Library\bin`, puis PATH.
- `find_executable()` centralise la recherche.

### diagnostics.py

Diagnostic reel des dependances.

- Verifie les modules Python : `pypdf`, `pdfplumber`, `openpyxl`, `pdf2image`, `Pillow`, `pytesseract`.
- Teste vraiment Tesseract via `pytesseract.get_tesseract_version()`.
- Affiche la version et le chemin si Tesseract fonctionne.
- Affiche `TesseractNotFoundError` ou l'exception brute si Tesseract echoue.
- Verifie `pdf2image.convert_from_path`.
- Verifie Poppler via `portable_paths.find_poppler_bin()`.

### ocr.py

OCR par page (local, Tesseract).

- `_configure_tesseract()` : localise et configure le binaire Tesseract.
- `_render_page_image(pdf_path, page_number, dpi)` : rasterise une page via `pdf2image` (reutilise aussi par `table_vision.py` et `claude_structuring.py` pour rester coherent).
- `_preprocess_image(image)` : niveaux de gris + `ImageOps.autocontrast`, degrade gracieusement en cas d'echec.
- `ocr_page(pdf_path, page_number, lang, dpi)` : texte brut OCR complet d'une page. Retourne `(texte, avertissements)`.
- `ocr_word_boxes(pdf_path, page_number, lang, dpi, min_confidence)` : mots OCR avec position en points PDF (rescale `72/dpi`), filtre par confiance Tesseract. Utilise par le repli "position des mots" quand la detection de grille echoue.

Erreurs gerees : modules OCR manquants, Tesseract introuvable, Poppler/conversion impossible, OCR vide.

### table_vision.py

Reconstruction de tableau scanne par grille de lignes reelle (OpenCV). Remplace un simple regroupement par position de mots, beaucoup plus fragile sur un vrai tableau a bordures.

- `deskew_image(pil_image)` : detecte l'angle dominant des longues lignes droites (transformee de Hough) et redresse l'image. Ne corrige que la rotation simple, pas la distorsion de perspective.
- `detect_table_cells(pil_image)` : isole les pixels de lignes horizontales/verticales longues (morphologie avec noyaux allonges), puis prend les composantes connexes du fond comme cellules. Une cellule fusionnee sur plusieurs lignes/colonnes est simplement une composante plus grande - pas de logique de fusion separee. Retourne `{"grid_rows", "grid_cols", "placements"}` ou `None` si aucune grille fiable.
- `ocr_grid_table(pil_image, lang)` : UNE seule passe `pytesseract.image_to_data` sur toute l'image (et non un appel par cellule - teste et rejete car ~1s de cout fixe par appel, prohibitif sur un tableau de 100 cellules), puis assignation de chaque mot reconnu a sa cellule de grille par position. Le texte d'une cellule fusionnee est reporte ("fill-down") sur toutes les lignes/colonnes qu'elle couvre. Signale par avertissement les cellules fusionnees sur lignes ET colonnes a la fois (risque de deux colonnes reunies par erreur si une bordure est absente sur le scan).

Toutes les fonctions echouent proprement (`None`/`ImportError`) si `opencv-python-headless`/`numpy` sont absents : le pipeline se rabat alors sur le niveau suivant.

### claude_structuring.py

Structuration systematique par l'API Claude (vision), utilisee si `--use-claude`.

- `STRUCTURING_PROMPT` : instructions envoyees a Claude - format JSON strict `{"tables": [...], "text_blocks": [...]}`, regles de fidelite (pas de traduction/resume/correction), regle de fill-down pour cellules fusionnees.
- `get_api_key()` : lit `ANTHROPIC_API_KEY` depuis l'environnement - jamais stockee dans le code.
- `render_page_png_bytes(pdf_path, page_number, dpi)` : rasterise la page (reutilise `ocr._render_page_image`) et encode en PNG.
- `structure_page_with_claude(pdf_path, page_number, api_key, model, dpi, max_retries)` : appelle `client.messages.create()` avec l'image + le prompt. Toute exception (reseau, cle invalide, proxy, reponse malformee) est capturee et transformee en repli propre plutot que de faire planter le traitement - avec une tentative de plus (`max_retries`) avant abandon definitif pour cette page. Retourne `(page_ou_None, avertissements, usage_tokens_ou_None)`.
- `parse_claude_json(raw_text)` : extrait le JSON de la reponse, tolerant les blocs markdown ```` ```json ```` que Claude ajoute parfois malgre la consigne contraire, et le texte parasite avant/apres.
- `build_extracted_page(parsed, page_number)` : convertit le JSON valide en `ExtractedPage`/`ExtractedTable` du modele interne, pour reutiliser tout le pipeline d'export existant (`detection_method="claude"`, confiance fixe a 0.97).
- `estimate_cost_usd(total_usage)` : estimation indicative a partir de `MODEL_PRICING_PER_MTOK` (tarifs a jour a la redaction, verifier la console Anthropic pour la facturation exacte).

### claude_reorganization.py

Reorganisation document-entier, utilisee si `--smart-merge`. Passe SUPPLEMENTAIRE apres l'extraction page par page (locale ou Claude) : envoie un resume texte (pas d'images) de tous les tableaux deja extraits, pour que Claude decide des fusions/regroupements avec une vision d'ensemble du document que l'extraction page par page n'a pas.

- `build_document_summary(result)` : resume compact de chaque tableau (page, `table_id`, en-tetes, nombre de colonnes/lignes, jusqu'a 2 lignes d'exemple) - volontairement pas le contenu integral, pour rester bon marche meme sur un document a des centaines de tableaux. Plafonne a `MAX_TABLES_IN_SUMMARY` (400) par securite.
- `REORGANIZATION_PROMPT` : demande a Claude de distinguer deux cas - `merged: true` (memes tableaux qui se poursuivent sur des pages consecutives, a fusionner) vs `merged: false` (tableaux distincts de meme nature, juste a regrouper). Consigne explicite de ne pas forcer un rapprochement douteux (`ungrouped_table_ids`). Demande aussi un champ `reason` (justification courte, affichee telle quelle dans l'Excel final - la reorganisation doit rester comprehensible, pas une boite noire).
- `reorganize_document_with_claude(result, api_key, model, max_retries)` : appelle Claude en texte seul (`content` avec deux blocs `text`, pas d'image). Meme philosophie de robustesse que `claude_structuring.py` : toute exception est capturee, retourne `(None, avertissements, None)` plutot que de lever - le resultat page par page reste alors inchange. Retourne `(plan, avertissements, usage_tokens)`.
- `parse_reorganization_json(raw_text)` : meme logique de nettoyage que `parse_claude_json` (blocs markdown, texte parasite).
- `apply_reorganization_plan(result, plan)` : traduit le plan JSON de Claude en objets `ConsolidatedGroup` reels. Defensif : un `table_id` mentionne par Claude mais introuvable dans `result` est ignore avec avertissement ; un groupe resolu a moins de 2 tableaux valides est abandonne (fusionner un seul tableau n'a pas de sens).
- `merge_tables(tables, title)` : fusionne plusieurs `ExtractedTable` en un seul - garde l'en-tete du premier tableau, complete les lignes plus courtes que `max_cols`, ignore les lignes qui ne font que repeter l'en-tete (frequent quand un tableau rendu page par page repete son en-tete a chaque page). Avertit si les tableaux fusionnes n'ont pas tous le meme nombre de colonnes plutot que d'echouer silencieusement. Ajoute une colonne `SOURCE_PAGE_COLUMN_LABEL` ("Page source") a la fin de chaque ligne, remplie avec `p.<numero de page>` : la fusion reste tracable ligne par ligne, pas fondue anonymement.

### pdf_extract.py

Extraction PDF principale (moteur local).

`extract_pdf()` (chemin pdfplumber, prioritaire)

1. Pour chaque page : `page.extract_text(layout=True)` (preserve l'espacement entre colonnes, indispensable pour la detection par regex).
2. `detect_page_tables()` : cascade a 3 niveaux (voir plus bas).
3. `page_is_scanned_image(page)` : detecte si une grande image recouvre l'essentiel de la page - sert a ne jamais faire confiance a un texte "natif" qui serait en realite une couche d'OCR de mauvaise qualite integree par un scanner/photocopieur.
4. Si `--ocr` et `looks_unreliable(text, tables, is_scanned_image)` : tente d'abord `try_ocr_grid_table()` (une seule passe OCR, texte de page derive du contenu de la grille pour eviter un DEUXIEME appel OCR complet) ; si echec, repli sur `ocr_page()` + `extract_tables_from_ocr(..., skip_grid=True)` (position de mots puis regex).
5. `finalize_table()` sur chaque tableau retenu.

`extract_pdf_with_claude(pdf_path, api_key, model, ...)`

- `count_pdf_pages()` determine le nombre total de pages (pdfplumber, repli pypdf).
- Pour chaque page selectionnee : `structure_page_with_claude()`. Si `None`, repli sur `extract_pdf()` pour cette seule page (avec `--ocr` local si demande), avertissement explicite ajoute. Sinon, page Claude utilisee directement.
- Retourne `(ExtractionResult, usage_log)` - `usage_log` sert a `estimate_cost_usd()` dans `cli.py`.

`extract_pdf_with_pypdf()` (repli si `pdfplumber` absent)

- Utilise `page.extract_text(extraction_mode="layout")` quand disponible.
- Applique les heuristiques regex/OCR historiques (mode simplifie, sans detection de grille systematique).

Detection de tableaux a 3 niveaux (`detect_page_tables`)

1. `extract_tables_with_pdfplumber()` : bordures reelles (`page.extract_tables()`), rejette les resultats a 1 colonne (souvent une table des matieres).
2. Detection par regex sur le texte a espacement preserve (`detect_tables` + `flush_table_candidate`, minimum 3 lignes pour limiter les faux positifs).
3. `extract_tables_by_word_position()` : position reelle des mots (`page.extract_words()`), seulement tente si les niveaux 1-2 n'ont rien trouve de quasi-parfait (`NEAR_PERFECT_TABLE_QUALITY`), pour eviter le cout sur de gros documents.

`table_set_quality()` / `fill_ratio()` : scores utilises pour arbitrer entre les niveaux et decider si l'OCR doit prendre le relais.

Post-traitement (`finalize_table`)

- `flag_possible_multiline_header()` : signale (sans fusionner - fusion automatique jugee trop risquee) qu'un en-tete pourrait etre reparti sur 2 lignes.
- `row_looks_like_header(row, data_rows)` : heuristique conservatrice - rejette l'hypothese "en-tete" seulement si la MAJORITE des cellules non vides ressemblent a un code court (`SHORT_CODE_RE`) ou une valeur numerique (`NUMERIC_LIKE_RE`), pas si une seule cellule le fait (un intitule de colonne peut legitimement etre un sigle court, ex. "CRI").

Texte libre (`group_into_paragraphs`)

- Regroupe les lignes consecutives de `kind="text"` en un seul paragraphe (une "zone de texte" dans l'Excel), au lieu d'une ligne source par ligne. Les lignes `kind="table"` ne sont jamais fusionnees.

Selection de pages

- `parse_page_selection("1,3,5-7", total_pages)` retourne `[1, 3, 5, 6, 7]`.
- Les pages hors bornes sont ignorees.

Modes

- `tables` : conserve surtout les tableaux.
- `text` : conserve le texte (en paragraphes), supprime les tableaux.
- `both` : conserve les deux.

### analyzer.py

Couche de nettoyage et de typage entre extraction et export.

`analyze(result, mode)`

1. Supprime les tableaux avec moins de deux lignes.
2. Supprime les lignes de tableau entierement vides.
3. Normalise les espaces dans les cellules.
4. `dedupe_repeated_headers()` : suit l'en-tete du tableau precedent a travers tout le document et supprime un en-tete identique repete (tableau qui continue sur la page suivante, ou plusieurs blocs issus d'un seul vrai tableau coupe par la mise en page).
5. Applique le mode : `tables`, `text`, `both`.
6. Ajoute un avertissement si une page est vide ou non exploitable.

Classification de cellules (`classify_cell`)

- Determine si une valeur est un nombre, une date ou du texte, en lecture seule (n'altere jamais la donnee source) - utilise par `export_excel.py` pour appliquer le bon type/format Excel.
- `parse_date()` : formats `DD/MM/YYYY`, `DD-MM-YYYY`, `DD.MM.YYYY`, ISO `YYYY-MM-DD`.
- `parse_number()` : gere les symboles monetaires (`CURRENCY_TOKENS`), le pourcentage (divise par 100), et desambiguise separateur de milliers/decimale francais vs anglo-saxon.

### export_excel.py

Generation du classeur `.xlsx`.

`export_to_excel(result, output_dir, mode)`

- Cree `Synthese` (avec liens vers chaque page).
- Cree `Tableaux_consolides` UNIQUEMENT si `result.consolidated_groups` est non vide (donc uniquement avec `--smart-merge` reussi) - purement additif, place juste apres `Synthese`, n'affecte pas les feuilles par page.
- Cree une feuille par page traitee (inchangees, que `--smart-merge` soit utilise ou non).
- Cree `Metadonnees`.
- Cree `Controle_qualite` (liste aussi les tableaux a confiance < `LOW_CONFIDENCE_THRESHOLD`, avec lien).
- Sauvegarde le fichier avec fallback horodate si le fichier est ouvert/verrouille.

`write_consolidated_sheet` / `write_consolidated_group`

- Bande de cartes KPI en haut de la feuille (via `write_kpi_cards()`, le meme composant que sur `Synthese`) : groupes, tableaux fusionnes, tableaux regroupes, tableaux d'origine impliques, pages couvertes.
- Pour chaque `ConsolidatedGroup` : un badge colore FUSIONNE/REGROUPE (`MERGED_BADGE_FILL`/`GROUPED_BADGE_FILL`), un titre de section (categorie + pages sources + nombre de tableaux d'origine), la justification de Claude (`group.reason`, en italique), puis reutilise `write_table_block()` pour chaque tableau du groupe - exactement le meme rendu que sur une feuille de page, pas de logique de rendu dupliquee. Pour un groupe REGROUPE (non fusionne), chaque appel a `write_table_block()` recoit un `extra_caption` rappelant la page d'origine de ce tableau precis.

`write_kpi_cards(sheet, kpis, row_number, row_label, start_col, card_width)`

- Composant partage entre `write_summary_sheet()` et `write_consolidated_sheet()` pour dessiner une rangee de cartes KPI identique visuellement partout. Colore une carte en alerte (fond/texte orange) si son 3e element de tuple (`is_warning_kpi`) est vrai ET que sa valeur est positive.

Tableaux Excel natifs (`add_excel_table`, `safe_table_name`)

- Chaque tableau detecte devient un objet `Table` openpyxl natif (filtre + bandes de couleur), independant des autres - contrairement a `sheet.auto_filter.ref` qui ne peut exister qu'une fois par feuille. `used_table_names` (un set cree par appel a `export_to_excel`, jamais un global module) garantit des noms uniques sans collision entre documents traites dans le meme processus (`--batch-dir`).

Cellules typees (`write_table_block`)

- Utilise `classify_cell()` pour ecrire un nombre/date natif avec le bon `number_format`, plutot que du texte brut partout.
- `detection_method_label()` traduit `detection_method` en libelle lisible affiche dans le titre du bloc tableau (bordures du PDF / alignement du texte / position des mots / grille de lignes / Claude).

Texte libre (`write_text_block`)

- Affiche les paragraphes regroupes (voir `group_into_paragraphs`), avec une hauteur de ligne estimee en fonction de la longueur pour rester lisible sans redimensionnement manuel.

Fonctions principales :

- `write_summary_sheet()` : statistiques globales, liens vers chaque page.
- `write_page_sheet()` : contenu page par page, lien retour vers la synthese.
- `write_table_block()` : bloc tableau stylise, typage des cellules, tableau Excel natif.
- `write_text_block()` : texte libre en paragraphes.
- `write_metadata_sheet()` : metadonnees.
- `write_quality_sheet()` : avertissements + tableaux a faible confiance.
- `safe_cell_value()` : retire les caracteres invisibles dangereux pour Excel.

### json_export.py

Export/import JSON intermediaire.

- `export_to_json(result, output_dir, mode)` : convertit les dataclasses en dictionnaire (`asdict`), ajoute source, date d'export, version, mode, resume. Ecrit en UTF-8, cree un fichier horodate en repli si le fichier cible est inaccessible.
- `import_from_json(json_path)` : reconstruit un `ExtractionResult` complet depuis un JSON precedemment exporte (`extraction_result_from_dict`, `page_from_dict`, `table_from_dict`, `row_from_dict` - avec valeurs par defaut retro-compatibles pour `has_header`/`detection_method` sur d'anciens JSON). Leve `ValueError` avec message clair si un champ obligatoire manque. Retourne aussi le `mode` enregistre lors de l'export d'origine.
- Utilise par `cli.py --from-json` pour regenerer l'Excel sans refaire l'extraction ni l'OCR/Claude.

## Tests

### tests/test_pdf_extract.py

Tests unitaires simples :

- decoupage avec `|` ;
- decoupage avec `;` ;
- classification texte/table ;
- detection d'un tableau depuis lignes consecutives.

### smoke_test.py

Test de bout en bout recommande :

```bash
python smoke_test.py
```

Doit afficher :

```text
SMOKE TEST OK
```

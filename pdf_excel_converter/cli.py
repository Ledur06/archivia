from __future__ import annotations

import argparse
from pathlib import Path

from .analyzer import analyze
from .claude_reorganization import DEFAULT_REORG_MODEL, apply_reorganization_plan, reorganize_document_with_claude
from .claude_structuring import DEFAULT_MODEL as DEFAULT_CLAUDE_MODEL
from .claude_structuring import estimate_cost_usd, get_api_key
from .diagnostics import format_diagnostics
from .export_excel import export_to_excel
from .json_export import export_to_json, import_from_json
from .pdf_extract import extract_pdf, extract_pdf_with_claude


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-excel-converter",
        description="Convertit des PDF natifs ou scannes en fichiers Excel structures.",
    )
    parser.add_argument("pdf_path", nargs="?", help="Chemin du fichier PDF source")
    parser.add_argument(
        "--output-dir",
        default="outputs/excel",
        help="Dossier de sortie pour le fichier Excel",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Affiche les dependances disponibles pour PDF natif, scan et OCR",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Active l'OCR pour les pages scannees si Tesseract et Poppler sont disponibles",
    )
    parser.add_argument(
        "--ocr-lang",
        default="fra+eng",
        help="Langues Tesseract a utiliser en OCR, par exemple fra+eng ou eng",
    )
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=300,
        help="Resolution (DPI) utilisee pour convertir les pages en image avant OCR. 300 par defaut, monter a 400 pour du petit texte.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limite le nombre de pages traitees pour tester rapidement un gros PDF",
    )
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        metavar="PAGES",
        help="Pages a traiter. Exemples : '3', '1,4,7', '2-5', '1,3,10-15'. Par defaut : toutes.",
    )
    parser.add_argument(
        "--mode",
        choices=["tables", "text", "both"],
        default=None,
        help="Contenu a extraire : tables, text ou both. Par defaut : 'both', ou le mode enregistre dans le JSON avec --from-json.",
    )
    parser.add_argument(
        "--from-json",
        type=str,
        default=None,
        metavar="JSON_PATH",
        help="Recharge une extraction deja faite depuis un JSON (produit par une conversion precedente) et regenere l'Excel sans refaire l'extraction ni l'OCR.",
    )
    parser.add_argument(
        "--batch-dir",
        type=str,
        default=None,
        metavar="DOSSIER",
        help="Traite tous les PDF d'un dossier a la suite, avec les memes options (--ocr, --mode, etc.).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Termine avec un code de sortie non nul si des pages sont non exploitables ou si des tableaux ont une confiance faible.",
    )
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Structure systematiquement CHAQUE page via l'API Claude (vision) au lieu des heuristiques locales. "
        "Necessite la variable d'environnement ANTHROPIC_API_KEY. En cas d'echec de l'appel pour une page "
        "donnee, se rabat automatiquement sur l'extraction locale pour cette page uniquement.",
    )
    parser.add_argument(
        "--claude-model",
        default=DEFAULT_CLAUDE_MODEL,
        help=f"Modele Claude a utiliser avec --use-claude. Par defaut : {DEFAULT_CLAUDE_MODEL} (rapide, economique).",
    )
    parser.add_argument(
        "--claude-dpi",
        type=int,
        default=150,
        help="Resolution (DPI) de l'image de page envoyee a Claude avec --use-claude. 150 par defaut (suffisant pour la lecture, limite le cout).",
    )
    parser.add_argument(
        "--smart-merge",
        action="store_true",
        help="Ajoute une passe de reorganisation document-entier apres l'extraction : Claude etudie tous les "
        "tableaux deja extraits (texte seul, sans images - cout marginal) pour fusionner ceux qui continuent "
        "sur plusieurs pages et regrouper ceux de meme nature. Necessite ANTHROPIC_API_KEY. N'affecte jamais "
        "les feuilles par page existantes : ajoute une feuille 'Tableaux_consolides' en plus. Fonctionne aussi "
        "avec --from-json.",
    )
    parser.add_argument(
        "--reorg-model",
        default=DEFAULT_REORG_MODEL,
        help=f"Modele Claude a utiliser pour --smart-merge. Par defaut : {DEFAULT_REORG_MODEL} (meilleur jugement organisationnel).",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.diagnose:
        print(format_diagnostics())
        return 0

    output_dir = Path(args.output_dir).expanduser().resolve()

    if args.smart_merge and not get_api_key():
        parser.error(
            "--smart-merge necessite la variable d'environnement ANTHROPIC_API_KEY (cle API Anthropic), "
            "meme si --use-claude n'est pas utilise par ailleurs."
        )

    if args.batch_dir:
        return run_batch(args, parser, output_dir)

    if args.from_json:
        return run_from_json(args, parser, output_dir)

    if not args.pdf_path:
        parser.error("Le chemin du PDF est obligatoire sauf avec --diagnose, --from-json ou --batch-dir.")

    pdf_path = Path(args.pdf_path).expanduser().resolve()

    if not pdf_path.exists():
        parser.error(f"Fichier introuvable: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        parser.error("Le fichier source doit etre un PDF.")

    mode = args.mode or "both"

    if args.use_claude and not get_api_key():
        parser.error(
            "--use-claude necessite la variable d'environnement ANTHROPIC_API_KEY (cle API Anthropic). "
            "Definissez-la puis relancez, par exemple : set ANTHROPIC_API_KEY=sk-ant-... (Windows) "
            "ou export ANTHROPIC_API_KEY=sk-ant-... (macOS/Linux)."
        )

    return convert_one(pdf_path, output_dir, args, mode)


def convert_one(pdf_path: Path, output_dir: Path, args: argparse.Namespace, mode: str) -> int:
    if getattr(args, "use_claude", False):
        api_key = get_api_key()
        result, usage_log = extract_pdf_with_claude(
            pdf_path,
            api_key=api_key,
            model=args.claude_model,
            max_pages=args.max_pages,
            pages_selection=args.pages,
            mode=mode,
            claude_dpi=args.claude_dpi,
            local_fallback_ocr=args.ocr,
            ocr_lang=args.ocr_lang,
            ocr_dpi=args.ocr_dpi,
        )
    else:
        result = extract_pdf(
            pdf_path,
            enable_ocr=args.ocr,
            max_pages=args.max_pages,
            ocr_lang=args.ocr_lang,
            pages_selection=args.pages,
            mode=mode,
            ocr_dpi=args.ocr_dpi,
        )
        usage_log = []

    result = analyze(result, mode=mode)

    if getattr(args, "smart_merge", False):
        usage_log = usage_log + run_smart_merge(result, args)

    output_path = export_to_excel(result, output_dir, mode=mode)
    json_path = export_to_json(result, output_dir, mode=mode)

    safe_print(f"Excel genere: {output_path}")
    safe_print(f"JSON genere: {json_path}")
    print_summary(result)

    if usage_log:
        cost = estimate_cost_usd(usage_log)
        safe_print(f"Pages structurees par Claude: {len(usage_log)}/{len(result.pages)}")
        safe_print(f"Cout API estime: ~${cost} USD (indicatif - voir votre console Anthropic pour le montant exact)")

    if result.consolidated_groups:
        safe_print(f"Groupes consolides (--smart-merge): {len(result.consolidated_groups)}")

    if args.strict and has_quality_issues(result):
        safe_print("Echec (--strict) : des pages ou tableaux necessitent une verification.")
        return 2

    return 0


def run_smart_merge(result, args: argparse.Namespace) -> list[dict]:
    """Lance la passe de reorganisation document-entier et applique le plan
    obtenu directement sur `result` (result.consolidated_groups). Retourne
    la liste d'usage token (vide si l'appel a echoue) pour l'estimation de
    cout globale. Ne leve jamais : en cas d'echec, ajoute simplement un
    avertissement et laisse le resultat page par page inchange."""
    api_key = get_api_key()
    if not api_key:
        result.warnings.append("--smart-merge ignore : ANTHROPIC_API_KEY manquante.")
        return []

    plan, reorg_warnings, reorg_usage = reorganize_document_with_claude(
        result, api_key=api_key, model=args.reorg_model
    )
    result.warnings.extend(reorg_warnings)

    if plan is not None:
        groups, apply_warnings = apply_reorganization_plan(result, plan)
        result.consolidated_groups = groups
        result.warnings.extend(apply_warnings)

    return [reorg_usage] if reorg_usage else []


def run_from_json(args: argparse.Namespace, parser: argparse.ArgumentParser, output_dir: Path) -> int:
    json_path = Path(args.from_json).expanduser().resolve()
    if not json_path.exists():
        parser.error(f"Fichier JSON introuvable: {json_path}")

    try:
        result, saved_mode = import_from_json(json_path)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - parser.error() leve SystemExit

    mode = args.mode or saved_mode or "both"

    usage_log: list[dict] = []
    if getattr(args, "smart_merge", False):
        usage_log = run_smart_merge(result, args)

    output_path = export_to_excel(result, output_dir, mode=mode)
    safe_print(f"Excel regenere depuis JSON: {output_path}")
    safe_print(f"Source JSON: {json_path}")
    print_summary(result)

    if usage_log:
        cost = estimate_cost_usd(usage_log)
        safe_print(f"Cout API estime (reorganisation): ~${cost} USD (indicatif)")

    if result.consolidated_groups:
        safe_print(f"Groupes consolides (--smart-merge): {len(result.consolidated_groups)}")

    if args.strict and has_quality_issues(result):
        safe_print("Echec (--strict) : des pages ou tableaux necessitent une verification.")
        return 2

    return 0


def run_batch(args: argparse.Namespace, parser: argparse.ArgumentParser, output_dir: Path) -> int:
    batch_dir = Path(args.batch_dir).expanduser().resolve()
    if not batch_dir.is_dir():
        parser.error(f"Dossier introuvable: {batch_dir}")

    pdf_files = sorted(batch_dir.glob("*.pdf"))
    if not pdf_files:
        safe_print(f"Aucun fichier PDF trouve dans {batch_dir}")
        return 1

    mode = args.mode or "both"
    exit_code = 0
    safe_print(f"Traitement par lot: {len(pdf_files)} fichier(s) PDF trouve(s) dans {batch_dir}")

    for index, pdf_path in enumerate(pdf_files, start=1):
        safe_print(f"\n[{index}/{len(pdf_files)}] {pdf_path.name}")
        try:
            file_exit_code = convert_one(pdf_path, output_dir, args, mode)
        except Exception as exc:  # noqa: BLE001 - on veut continuer le lot meme si un PDF echoue
            safe_print(f"Echec sur {pdf_path.name}: {type(exc).__name__} - {exc}")
            exit_code = 1
            continue
        if file_exit_code != 0:
            exit_code = file_exit_code

    return exit_code


def print_summary(result) -> None:
    safe_print(f"Pages traitees: {len(result.pages)}")
    safe_print(f"Tableaux detectes: {result.total_tables}")
    safe_print(f"Lignes extraites: {result.total_rows}")
    if result.warnings:
        safe_print("Avertissements:")
        for warning in result.warnings:
            safe_print(f"- {warning}")


def has_quality_issues(result) -> bool:
    if result.warnings:
        return True
    for page in result.pages:
        for table in page.tables:
            if table.confidence < 0.8:
                return True
    return False


def safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", errors="backslashreplace").decode("ascii"))

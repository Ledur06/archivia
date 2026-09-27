#!/usr/bin/env bash
# convert.sh - lanceur Linux/Mac pour PDF Excel Converter
# Usage : ./convert.sh --pdf chemin/document.pdf [--ocr] [--pages "1,3-5"] [--mode tables] [--max-pages 10]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PDF=""
OCR=""
PAGES=""
MODE="both"
MAX_PAGES=""
OCR_LANG="fra+eng"
OUTPUT_DIR="outputs/excel"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pdf) PDF="$2"; shift 2 ;;
    --ocr) OCR="--ocr"; shift ;;
    --ocr-lang) OCR_LANG="$2"; shift 2 ;;
    --pages) PAGES="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --max-pages) MAX_PAGES="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    *) echo "Option inconnue : $1"; exit 1 ;;
  esac
done

if [[ -z "$PDF" ]]; then
  echo "Usage : ./convert.sh --pdf chemin/document.pdf [options]"
  exit 1
fi

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

CMD=("$PYTHON_BIN" -m pdf_excel_converter "$PDF" --output-dir "$OUTPUT_DIR" --mode "$MODE")

if [[ -n "$OCR" ]]; then
  CMD+=(--ocr --ocr-lang "$OCR_LANG")
fi
if [[ -n "$PAGES" ]]; then
  CMD+=(--pages "$PAGES")
fi
if [[ -n "$MAX_PAGES" ]]; then
  CMD+=(--max-pages "$MAX_PAGES")
fi

"${CMD[@]}"

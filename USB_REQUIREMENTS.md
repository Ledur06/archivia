# Requirements pour Utilisation sur Cle USB

Ce document explique comment preparer l'outil pour qu'il fonctionne sur un autre ordinateur Windows apres transfert par cle USB.

## Structure recommandee de la cle USB

```text
PDF_Excel_Converter/
|-- Convert-PDFToExcel.ps1
|-- convert.sh
|-- README.md
|-- requirements.txt
|-- pdf_excel_converter/
|-- tests/
|-- portable/
|   |-- python/
|   |   |-- python.exe
|   |-- Tesseract-OCR/
|   |   |-- tesseract.exe
|   |   |-- tessdata/
|   |       |-- fra.traineddata
|   |       |-- eng.traineddata
|   |-- poppler/
|       |-- bin/
|           |-- pdftoppm.exe
```

## 1. Python

Deux options sont possibles.

Option A - Python installe sur le PC :

```powershell
python --version
pip install -r requirements.txt
```

Option B - Python portable dans la cle USB :

- placer Python dans `portable\python\` ;
- verifier que `portable\python\python.exe` existe ;
- installer les dependances dans ce Python portable.

Le lanceur `Convert-PDFToExcel.ps1` cherche d'abord :

```text
portable\python\python.exe
```

Puis il utilise le Python du systeme si le Python portable n'existe pas.

## 2. Dependances Python

Installer :

```powershell
pip install -r requirements.txt
```

Dependances :

- `pdfplumber` : lecture PDF native et extraction geometrique des tableaux ;
- `pypdf` : fallback texte si `pdfplumber` est absent ;
- `openpyxl` : generation Excel ;
- `pdf2image` : conversion PDF vers image pour OCR ;
- `pillow` : manipulation image ;
- `pytesseract` : appel Python vers Tesseract.

## 3. Tesseract OCR

Necessaire pour les PDF scannes.

L'outil cherche Tesseract dans :

```text
portable\Tesseract-OCR\tesseract.exe
portable\tesseract\tesseract.exe
tools\Tesseract-OCR\tesseract.exe
tools\tesseract\tesseract.exe
PATH Windows
```

Langues recommandees dans `tessdata` :

- `fra.traineddata`
- `eng.traineddata`

## 4. Poppler

Necessaire pour convertir les pages PDF en images avant OCR.

L'outil cherche Poppler dans :

```text
portable\poppler\bin\pdftoppm.exe
tools\poppler\bin\pdftoppm.exe
PATH Windows
```

## 5. Verification avant partage

Depuis le dossier de la cle USB :

```powershell
.\Convert-PDFToExcel.ps1 -PdfPath "exemple.pdf" -MaxPages 2
python -m pdf_excel_converter --diagnose
```

Sur Linux/Mac :

```bash
chmod +x convert.sh
./convert.sh --pdf exemple.pdf --max-pages 2
python3 -m pdf_excel_converter --diagnose
```

Pour tester OCR :

```powershell
.\Convert-PDFToExcel.ps1 -PdfPath "scan.pdf" -Ocr -MaxPages 2
```

Si Tesseract et Poppler sont installes dans des dossiers systeme :

```powershell
.\Convert-PDFToExcel.ps1 -PdfPath "scan.pdf" -Ocr -TesseractPath "C:\Program Files\Tesseract-OCR" -PopplerBin "C:\poppler\Library\bin"
```

## 6. Points importants

- Garder tous les fichiers du projet dans le meme dossier.
- Ne pas renommer `pdf_excel_converter`.
- Installer `pdfplumber` pour obtenir la meilleure detection de tableaux.
- Tesseract et Poppler ne sont pas des paquets pip : il faut installer/copier leurs binaires.
- Pour OCR, Tesseract et Poppler doivent etre soit installes sur le PC, soit presents dans `portable`.
- Si Excel refuse d'ecraser un fichier deja ouvert, l'outil cree automatiquement un nouveau fichier horodate.

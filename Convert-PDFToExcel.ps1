param(
    [Parameter(Mandatory=$true)]
    [string]$PdfPath,

    [string]$OutputDir = "$env:USERPROFILE\Documents\PDF_EXCEL_OUTPUT",

    [switch]$Ocr,

    [string]$OcrLang = "fra+eng",

    [string]$TesseractPath = "",

    [string]$PopplerBin = "",

    [string]$Pages = "",

    [ValidateSet("tables", "text", "both")]
    [string]$Mode = "both",

    [int]$MaxPages = 0
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutputDirPath = if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir
}
else {
    Join-Path $ProjectDir $OutputDir
}

$PythonExe = & python -c "import sys; print(sys.executable)" 2>$null
if (-not $PythonExe) {
    $PortablePython = Join-Path $ProjectDir "portable\python\python.exe"
    if (Test-Path -LiteralPath $PortablePython) {
        $PythonExe = $PortablePython
    }
    else {
        $PythonExe = "python"
    }
}

Push-Location $ProjectDir
try {
    $UserSite = & $PythonExe -c "import site; print(site.getusersitepackages())" 2>$null
    if ($UserSite -and (Test-Path -LiteralPath $UserSite)) {
        if ($env:PYTHONPATH) {
            $env:PYTHONPATH = "$UserSite;$ProjectDir;$env:PYTHONPATH"
        }
        else {
            $env:PYTHONPATH = "$UserSite;$ProjectDir"
        }
    }

    if ($TesseractPath) {
        if (Test-Path -LiteralPath $TesseractPath) {
            $env:PATH += ";$TesseractPath"
        }
        else {
            Write-Warning "Chemin Tesseract introuvable: $TesseractPath"
        }
    }
    elseif (Test-Path -LiteralPath "C:\Program Files\Tesseract-OCR") {
        $env:PATH += ";C:\Program Files\Tesseract-OCR"
    }

    if ($PopplerBin) {
        if (Test-Path -LiteralPath $PopplerBin) {
            $env:PATH += ";$PopplerBin"
        }
        else {
            Write-Warning "Chemin Poppler introuvable: $PopplerBin"
        }
    }
    elseif (Test-Path -LiteralPath "C:\poppler\Library\bin") {
        $env:PATH += ";C:\poppler\Library\bin"
    }

    $ArgsList = @("-m", "pdf_excel_converter", $PdfPath, "--output-dir", $OutputDirPath, "--mode", $Mode)
    if ($Ocr) {
        $ArgsList += @("--ocr", "--ocr-lang", $OcrLang)
    }
    if ($Pages) {
        $ArgsList += @("--pages", $Pages)
    }
    if ($MaxPages -gt 0) {
        $ArgsList += @("--max-pages", $MaxPages)
    }

    & $PythonExe @ArgsList

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Get-ChildItem -LiteralPath $OutputDirPath -Filter "*.xlsx" -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            Unblock-File -LiteralPath $_.FullName -ErrorAction SilentlyContinue
        }
}
finally {
    Pop-Location
}

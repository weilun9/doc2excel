# Build dist\Doc2Excel.exe (single file). Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File build.ps1
# If NSIS (makensis) is installed, also builds dist\Doc2Excel-Setup-<version>.exe
# from installer\doc2excel.nsi.
param([string]$Version = "1.1.1")
# PyInstaller logs to stderr, so don't use ErrorActionPreference=Stop (PowerShell 5
# would treat every log line as an error); check exit codes instead.
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    py -3.12 -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
}
.\.venv\Scripts\python assets\make_icon.py
.\.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed `
    --name Doc2Excel `
    --icon assets\icon.ico `
    --add-data "app\web;app\web" `
    --add-data "assets\dynamod;assets\dynamod" `
    --collect-data rapidocr_onnxruntime `
    --collect-binaries onnxruntime `
    --hidden-import pillow_heif `
    --exclude-module tkinter --exclude-module matplotlib `
    run.py
if ($LASTEXITCODE -ne 0) { Write-Host "Build FAILED ($LASTEXITCODE)"; exit $LASTEXITCODE }
Copy-Item .env.example dist\.env.example -Force
Write-Host "Built: $(Resolve-Path dist\Doc2Excel.exe)"
$nsis = Get-Command makensis -ErrorAction SilentlyContinue
if (-not $nsis -and (Test-Path "${env:ProgramFiles(x86)}\NSIS\makensis.exe")) { $nsis = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
if ($nsis) {
    & $nsis /V2 "/DVERSION=$Version" installer\doc2excel.nsi
    if ($LASTEXITCODE -ne 0) { Write-Host "Installer FAILED ($LASTEXITCODE)"; exit $LASTEXITCODE }
    Write-Host "Installer: $(Resolve-Path dist\Doc2Excel-Setup-$Version.exe)"
} else {
    Write-Host "NSIS not found: skipped the installer (winget install NSIS.NSIS)"
}

# Build dist\Doc2Excel.exe (single file). Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Version 1.3.0 [-Edition internal|public]
# If NSIS (makensis) is installed, also builds dist\Doc2Excel-Setup-<version>.exe
# from installer\doc2excel.nsi.
#   internal = "Doc2Excel Pro": Excel + SmartAcc DynaMod + send to AI ERP (default)
#   public   = "Doc2Excel": Excel only; installs and keeps data apart from Pro
param(
    [string]$Version = "1.1.2",
    [ValidateSet("internal", "public")][string]$Edition = "internal"
)
# PyInstaller logs to stderr, so don't use ErrorActionPreference=Stop (PowerShell 5
# would treat every log line as an error); check exit codes instead.
Set-Location $PSScriptRoot
if ($Version -notmatch '^\d+(\.\d+){1,3}$') { Write-Host "Bad -Version '$Version' (expected e.g. 1.1.2)"; exit 1 }
if (-not (Test-Path .venv)) {
    py -3.12 -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
}
.\.venv\Scripts\python assets\make_icon.py
# The version and edition are baked into the exe only; a source checkout reports
# "dev" and the internal edition (see app\version.py, app\edition.py), so the
# files are removed again after packaging.
[IO.File]::WriteAllText("$PSScriptRoot\app\_build_version.py", "VERSION = `"$Version`"`n")
[IO.File]::WriteAllText("$PSScriptRoot\app\_build_edition.py", "EDITION = `"$Edition`"`n")
try {
    # Destination modules are loaded by name at run time, so PyInstaller is told
    # about this edition's ones; the others (and their code) are left out.
    $ed = (.\.venv\Scripts\python -c "import json; from app import edition as e; print(json.dumps({'id': e.APP_ID, 'name': e.NAME, 'dest': list(e.DESTINATIONS), 'all': list(e.EDITIONS['internal']['destinations'])}))") | ConvertFrom-Json
    if (-not $ed) { throw "could not read the edition" }
    $extra = @()
    foreach ($d in $ed.dest) { $extra += @("--hidden-import", "app.destinations.$d") }
    foreach ($d in $ed.all) {
        if ($ed.dest -notcontains $d) { $extra += @("--exclude-module", "app.destinations.$d", "--exclude-module", "app.$d") }
    }
    if ($ed.dest -contains "dynamod") { $extra += @("--add-data", "assets\dynamod;assets\dynamod") }
    Write-Host "Edition: $Edition ($($ed.name)); destinations: $($ed.dest -join ', ')"
    .\.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed `
        --name Doc2Excel `
        --icon assets\icon.ico `
        --add-data "app\web;app\web" `
        --collect-data rapidocr_onnxruntime `
        --collect-binaries onnxruntime `
        --hidden-import pillow_heif `
        --hidden-import app._build_version --hidden-import app._build_edition `
        --hidden-import win32cred --hidden-import win32timezone --hidden-import truststore `
        --exclude-module tkinter --exclude-module matplotlib `
        @extra `
        run.py
    $code = $LASTEXITCODE
} finally {
    Remove-Item "$PSScriptRoot\app\_build_version.py" -ErrorAction SilentlyContinue
    Remove-Item "$PSScriptRoot\app\_build_edition.py" -ErrorAction SilentlyContinue
}
if ($code -ne 0) { Write-Host "Build FAILED ($code)"; exit $code }
Copy-Item .env.example dist\.env.example -Force
Write-Host "Built: $(Resolve-Path dist\Doc2Excel.exe)"
$nsis = Get-Command makensis -ErrorAction SilentlyContinue
if (-not $nsis -and (Test-Path "${env:ProgramFiles(x86)}\NSIS\makensis.exe")) { $nsis = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
if ($nsis) {
    & $nsis /V2 "/DVERSION=$Version" "/DAPP_ID=$($ed.id)" "/DAPP_NAME=$($ed.name)" installer\doc2excel.nsi
    if ($LASTEXITCODE -ne 0) { Write-Host "Installer FAILED ($LASTEXITCODE)"; exit $LASTEXITCODE }
    Write-Host "Installer: $(Resolve-Path dist\Doc2Excel-Setup-$Version.exe)"
} else {
    Write-Host "NSIS not found: skipped the installer (winget install NSIS.NSIS)"
}

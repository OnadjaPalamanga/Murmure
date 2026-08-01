# Installe Murmure pour un usage quotidien : compile la version release et
# l'ajoute au demarrage de la session Windows.
#
#   .\install.ps1              installe
#   .\install.ps1 -Uninstall   retire le demarrage automatique
#
# Aucun privilege administrateur requis : tout se fait dans le profil utilisateur.

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "Murmure.lnk"

if ($Uninstall) {
    if (Test-Path $link) {
        Remove-Item $link -Force
        Write-Host "Demarrage automatique retire." -ForegroundColor Green
    } else {
        Write-Host "Murmure n'etait pas au demarrage."
    }
    Write-Host "Le dossier du projet et tes donnees sont intacts."
    Write-Host "Historique et reglages : $env:APPDATA\Murmure"
    return
}

# --- 1. Environnement Python ---------------------------------------------
$python = Join-Path $root "backend\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creation de l'environnement Python..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "backend")
    uv venv --python 3.12
    uv pip install -e .
    Pop-Location
}

# --- 2. Binaire release ---------------------------------------------------
$exe = Join-Path $root "frontend\src-tauri\target\release\murmure.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Compilation de l'application (quelques minutes)..." -ForegroundColor Cyan
    $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
    Push-Location (Join-Path $root "frontend")
    if (-not (Test-Path "node_modules")) { npm install }
    npx tauri build
    Pop-Location
}
if (-not (Test-Path $exe)) { throw "Compilation echouee : $exe introuvable." }

# --- 3. Demarrage automatique --------------------------------------------
# Le binaire retrouve seul le service Python en remontant l'arborescence, et le
# lance au besoin : il n'y a qu'un seul element a demarrer.
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path $exe
$shortcut.Description = "Murmure - dictee locale"
$shortcut.IconLocation = $exe
$shortcut.Save()

Write-Host ""
Write-Host "Murmure est installe." -ForegroundColor Green
Write-Host "  application : $exe"
Write-Host "  au demarrage: $link"
Write-Host "  donnees     : $env:APPDATA\Murmure"
Write-Host ""
Write-Host "Lance-le maintenant sans redemarrer :" -ForegroundColor Cyan
Write-Host "  Start-Process '$exe'"

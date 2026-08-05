# Installe Murmure pour un usage quotidien : compile la version release, la pose
# dans le menu Demarrer et l'ajoute au demarrage de la session Windows.
#
#   .\install.ps1              installe
#   .\install.ps1 -Uninstall   retire les raccourcis
#
# Aucun privilege administrateur requis : tout se fait dans le profil utilisateur.
#
# Le dossier du projet EST l'installation : les raccourcis pointent dessus, rien
# n'est copie ailleurs. C'est delibere — l'environnement Python pese 2,5 Go et
# les modeles 14 Go. Un installeur qui les dupliquerait creerait un second
# Murmure, avec sa propre version, et rien n'empecherait de lancer le mauvais.

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$programs = [Environment]::GetFolderPath("Programs")
$link = Join-Path $startup "Murmure.lnk"
$menu = Join-Path $programs "Murmure.lnk"

if ($Uninstall) {
    $removed = @()
    foreach ($path in @($link, $menu)) {
        if (Test-Path $path) { Remove-Item $path -Force; $removed += $path }
    }
    if ($removed) {
        Write-Host "Raccourcis retires :" -ForegroundColor Green
        $removed | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "Murmure n'avait aucun raccourci installe."
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
# On recompile des que le binaire est plus vieux qu'une source, jamais sur sa
# seule existence : un binaire perime se lance aussi bien qu'un neuf, et rien
# dans l'interface ne dit son age. Vecu — un release de quatre jours servait une
# ancienne version pendant que le code evoluait a cote.
$exe = Join-Path $root "frontend\src-tauri\target\release\murmure.exe"
$sources = @(
    (Join-Path $root "frontend\src"),
    (Join-Path $root "frontend\src-tauri\src"),
    (Join-Path $root "frontend\src-tauri\tauri.conf.json")
)
$newest = Get-ChildItem $sources -Recurse -File -EA SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

$stale = (-not (Test-Path $exe)) -or
         ($newest -and $newest.LastWriteTime -gt (Get-Item $exe).LastWriteTime)

if ($stale) {
    Write-Host "Compilation de l'application (quelques minutes)..." -ForegroundColor Cyan
    $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
    Push-Location (Join-Path $root "frontend")
    if (-not (Test-Path "node_modules")) { npm install }
    npx tauri build
    Pop-Location
} else {
    Write-Host "Binaire deja a jour." -ForegroundColor DarkGray
}
if (-not (Test-Path $exe)) { throw "Compilation echouee : $exe introuvable." }

# --- 3. Raccourcis ---------------------------------------------------------
# Deux raccourcis vers le MEME binaire : le menu Demarrer pour le lancer a la
# main (et le retrouver a la recherche, ou l'epingler), le dossier Demarrage
# pour qu'il soit la des l'ouverture de session. Le binaire retrouve seul le
# service Python en remontant l'arborescence : il n'y a qu'un element a lancer,
# et un second lancement rouvre la fenetre de l'instance en place au lieu d'en
# creer une deuxieme.
$shell = New-Object -ComObject WScript.Shell
foreach ($path in @($menu, $link)) {
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = Split-Path $exe
    $shortcut.Description = "Murmure - dictee locale instantanee"
    $shortcut.IconLocation = $exe
    $shortcut.Save()
}

Write-Host ""
Write-Host "Murmure est installe." -ForegroundColor Green
Write-Host "  application  : $exe"
Write-Host "  menu Demarrer: $menu"
Write-Host "  au demarrage : $link"
Write-Host "  donnees      : $env:APPDATA\Murmure"
Write-Host ""
Write-Host "Lance-le maintenant sans redemarrer :" -ForegroundColor Cyan
Write-Host "  Start-Process '$exe'"

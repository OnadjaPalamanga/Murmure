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

# --- 0. Prerequis ----------------------------------------------------------
# Verifies AVANT toute compilation. Sans ce controle, une chaine d'outils
# incomplete se manifeste au milieu d'un build de plusieurs minutes, par un
# « le terme n'est pas reconnu » qui ne dit ni quoi installer ni ou le trouver.
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

$outils = @(
    @{ Nom = "uv";    Commande = "uv";    Aide = "https://docs.astral.sh/uv/ - winget install astral-sh.uv" }
    @{ Nom = "Node";  Commande = "npm";   Aide = "https://nodejs.org/ (version 18 ou superieure)" }
    @{ Nom = "Rust";  Commande = "cargo"; Aide = "https://rustup.rs/ - puis rouvre ce terminal" }
)

$manquants = @()
foreach ($outil in $outils) {
    if (-not (Get-Command $outil.Commande -ErrorAction SilentlyContinue)) {
        $manquants += $outil
    }
}

if ($manquants) {
    Write-Host ""
    Write-Host "Outils manquants :" -ForegroundColor Red
    foreach ($outil in $manquants) {
        Write-Host ("  {0,-6} ({1})  ->  {2}" -f $outil.Nom, $outil.Commande, $outil.Aide)
    }
    Write-Host ""
    Write-Host "Installe-les, rouvre un terminal, puis relance .\install.ps1"
    throw "Prerequis absents : $($manquants.Nom -join ', ')"
}

# Les Build Tools MSVC (link.exe) ne sont pas dans le PATH tant qu'on n'est pas
# dans un invite developpeur : cargo les trouve seul via vswhere. On ne les
# cherche donc pas ici, mais on sait dire ce qui manque si l'edition des liens
# echoue plus bas.

# --- 1. Environnement Python ---------------------------------------------
$python = Join-Path $root "backend\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creation de l'environnement Python (plusieurs Go, quelques minutes)..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "backend")
    try {
        uv venv --python 3.12
        if ($LASTEXITCODE -ne 0) { throw "uv venv a echoue (code $LASTEXITCODE)." }
        uv pip install -e .
        if ($LASTEXITCODE -ne 0) { throw "uv pip install a echoue (code $LASTEXITCODE)." }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $python)) {
        throw "Environnement Python incomplet : $python introuvable."
    }
}

# --- 2. Binaire release ---------------------------------------------------
# On recompile des que le binaire est plus vieux qu'une source, jamais sur sa
# seule existence : un binaire perime se lance aussi bien qu'un neuf, et rien
# dans l'interface ne dit son age. Vecu — un release de quatre jours servait une
# ancienne version pendant que le code evoluait a cote.
#
# La liste couvre tout ce qui change le binaire, pas seulement le code :
# `Cargo.toml` (dependances, profil de compilation) et `capabilities\` (ce que
# le frontend a le droit d'appeler) sont compiles dans l'executable. Les
# oublier laissait passer un retrait de permission ou de plugin sans rebuild —
# et le binaire installe gardait la permission qu'on croyait avoir retiree.
$exe = Join-Path $root "frontend\src-tauri\target\release\murmure.exe"
$sources = @(
    (Join-Path $root "frontend\src"),
    (Join-Path $root "frontend\src-tauri\src"),
    (Join-Path $root "frontend\src-tauri\capabilities"),
    (Join-Path $root "frontend\src-tauri\tauri.conf.json"),
    (Join-Path $root "frontend\src-tauri\Cargo.toml")
)
$newest = Get-ChildItem $sources -Recurse -File -EA SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

$stale = (-not (Test-Path $exe)) -or
         ($newest -and $newest.LastWriteTime -gt (Get-Item $exe).LastWriteTime)

if ($stale) {
    Write-Host "Compilation de l'application (quelques minutes)..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "frontend")
    try {
        if (-not (Test-Path "node_modules")) {
            npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install a echoue (code $LASTEXITCODE)." }
        }
        npx tauri build
        if ($LASTEXITCODE -ne 0) {
            throw @"
La compilation a echoue (code $LASTEXITCODE).

Cause la plus frequente sous Windows : les Build Tools MSVC manquent, et
l'edition des liens echoue sur « link.exe introuvable ». Installe-les via
Visual Studio Installer > « Desktop development with C++ », puis relance.
"@
        }
    } finally {
        Pop-Location
    }
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

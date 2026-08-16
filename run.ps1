# Lance le service Python puis l'application. A epingler ou a mettre au demarrage.
#
#   .\run.ps1          lance en mode developpement (rechargement du frontend)
#   .\run.ps1 -Build   compile le binaire release
#
# -Build produit `frontend/src-tauri/target/release/murmure.exe`, PAS un
# installateur : `tauri.conf.json` porte "bundle": { "active": false }. Le
# dossier du projet est l'installation — voir install.ps1, qui pose simplement
# des raccourcis vers ce binaire.

param([switch]$Build)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\pythonw.exe"

if (-not (Test-Path $python)) {
    throw "Environnement Python absent. Lance d'abord : cd backend; uv venv --python 3.12; uv pip install -e ."
}

# Un seul service a la fois : s'il repond deja, on ne le relance pas.
$alive = $false
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8756/health" -TimeoutSec 1 | Out-Null
    $alive = $true
} catch {}

if (-not $alive) {
    Write-Host "Demarrage du service Murmure..." -ForegroundColor Cyan
    Start-Process -FilePath $python -ArgumentList "-m", "murmure" `
        -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden

    foreach ($i in 1..40) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:8756/health" -TimeoutSec 1 | Out-Null
            break
        } catch { Start-Sleep -Milliseconds 500 }
    }
}

$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
Set-Location (Join-Path $root "frontend")

if ($Build) {
    Write-Host "Compilation de l'installateur..." -ForegroundColor Cyan
    npx tauri build
} else {
    npx tauri dev
}

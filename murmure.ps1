# La ligne de commande de Murmure, sans avoir a connaitre le chemin du venv.
#
#   .\murmure.ps1 --help
#   .\murmure.ps1 transcribe .\rushes\*.mp4 -f srt,json -o .\sous-titres
#   .\murmure.ps1 models
#
# Simple relais vers `backend\.venv\Scripts\murmure.exe`, qui est le vrai
# programme : les arguments partent tels quels, y compris les jokers, que la
# commande developpe elle-meme (PowerShell ne le fait pas pour un executable).
#
# python.exe et non pythonw.exe : la sortie doit arriver dans la console, c'est
# tout l'interet. Le service, lui, reste lance sans fenetre par l'application.

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Environnement Python absent. Lance d'abord : cd backend; uv venv --python 3.12; uv pip install -e ."
    exit 1
}

& $python -m murmure @args

# Le code de sortie est celui de la commande : un pipeline s'y fie pour savoir
# si ses sous-titres ont ete produits.
exit $LASTEXITCODE

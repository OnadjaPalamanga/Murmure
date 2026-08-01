# Murmure

Dictée locale instantanée. Un raccourci clavier, tu parles, le texte est là.
Tout tourne sur ta machine : aucun appel réseau après le téléchargement des modèles.

## Pourquoi c'est rapide

Ce n'est pas le modèle qui fait la latence, c'est l'architecture :

- **Le modèle reste résident en VRAM.** Pas de rechargement à chaque dictée.
- **Le micro reste ouvert 90 s** après une dictée. Ouvrir un périphérique WASAPI
  coûte 50-150 ms ; en enchaînant, on ne le paie qu'une fois.
- **Un pré-enregistrement de 400 ms** tourne en permanence : le début de mot
  prononcé juste avant l'appui est déjà capturé.
- **Découpage sur les silences** pour les longs enregistrements : l'attention du
  Conformer est quadratique, dix passages de 45 s coûtent bien moins qu'un de 450 s.

Mesuré sur une RTX 2080, audio français de 474 s :

| Modèle | Temps | Facteur temps réel |
| --- | --- | --- |
| Parakeet v3 | 9,3 s | 51× |
| Whisper large-v3-turbo | 10,4 s | 46× |
| Whisper FR distil dec16 | 20,3 s | 23× |

Sur une dictée de 15 s : **111 ms**, modèle chaud.

## Installation

Prérequis : [uv](https://docs.astral.sh/uv/), Node 18+, Rust, et les
Build Tools MSVC (déjà présents si Visual Studio est installé).

```powershell
cd backend
uv venv --python 3.12
uv pip install -e .

cd ../frontend
npm install
```

## Lancement

```powershell
.\run.ps1            # service + application
.\run.ps1 -Build     # compile l'installateur .exe
```

Le premier démarrage télécharge le modèle par défaut (~600 Mo) dans `models/hub`.

## Utilisation

`Ctrl+Alt+Space` maintenu — parle — relâche. Le texte est copié dans le
presse-papier et affiché pour relecture. `Échap` annule.

L'icône dans la zone de notification donne accès à l'historique et aux réglages.
Fermer la fenêtre principale ne quitte pas : le raccourci reste actif.

## Modèles

Quatre modèles sont proposés, avec des réglages optimaux câblés par modèle.
**Parakeet v3 est le défaut** : le plus rapide, le meilleur en français parmi
les rapides, et son architecture *transducer* peut émettre une sortie vide au
lieu de forcer un token — il n'invente pas de texte sur les silences, contrairement
à Whisper.

Contrepartie : Parakeet détecte la langue seul et **on ne peut pas la forcer**.
Sur un enregistrement long, il lui arrive de dériver vers l'anglais. Si ça te
gêne, bascule sur `whisper-large-v3-turbo`, verrouillé en français.

> À ne pas faire : passer `language="fr"` à Parakeet. Le texte est rigoureusement
> identique et le temps de calcul est multiplié par 7,5. Vérifié, pas supposé.

### Ajouter ton propre modèle

Dépose un dossier dans `models/` :

- un `model.bin` → détecté comme CTranslate2 (Whisper)
- des `.onnx` → détecté comme onnx-asr (Parakeet, Canary…)

Un `murmure.json` optionnel à côté permet de nommer le modèle et de surcharger
ses options :

```json
{
  "label": "Mon modèle affiné",
  "languages": "français",
  "options": { "beam_size": 2, "initial_prompt": "Notes techniques ponctuées." }
}
```

## Architecture

```
backend/                 service Python (le modèle vit ici)
  src/murmure/
    audio.py             capture micro, tampon circulaire, pré-enregistrement
    engines/             moteurs interchangeables (Parakeet ONNX, faster-whisper)
    chunking.py          découpage sur les silences
    service.py           orchestration micro → moteur → historique
    server.py            transport WebSocket (aucune logique métier)
    history.py           SQLite + recherche plein texte FTS5
frontend/                application Tauri 2
  src/                   overlay + fenêtre principale (HTML/CSS/JS)
  src-tauri/src/lib.rs   raccourci global, zone de notification, positionnement
```

Le frontend ne parle au backend que par WebSocket sur `127.0.0.1:8756`.
Les deux se relancent indépendamment.

## Réglages

`%APPDATA%\Murmure\config.toml` — seules les valeurs qui diffèrent du défaut y
sont écrites, le fichier reste lisible. L'historique est dans `history.db` à côté.

## Vérifications

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\smoke.py <fichier.wav> [id_modele ...]
.\.venv\Scripts\python.exe scripts\ws_check.py 5
```

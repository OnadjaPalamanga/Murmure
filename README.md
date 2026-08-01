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
.\install.ps1
```

Le script crée l'environnement Python s'il manque, compile l'application, et
l'ajoute au démarrage de la session. Aucun privilège administrateur requis.

```powershell
.\install.ps1 -Uninstall   # retire du démarrage, ne touche pas aux données
.\run.ps1                  # mode développement, rechargement à chaud
```

**Il n'y a qu'une seule chose à lancer** : l'application démarre elle-même le
service Python si besoin, et l'arrête quand tu quittes par le menu système.

Le premier démarrage télécharge le modèle par défaut (~600 Mo) dans `models/hub`,
avec la progression affichée — pas d'écran figé sans explication.

## Utilisation

`Ctrl+Space` maintenu — parle — relâche. Le texte est copié dans le
presse-papier et affiché pour relecture. `Échap` annule.

> `Ctrl+Space` est aussi l'autocomplétion de la plupart des IDE, et un raccourci
> global le confisque à l'échelle du système. Si ça gêne, change-le dans Réglages ;
> un raccourci déjà pris par une autre application est signalé au lieu d'échouer
> en silence.

Trois autres façons de déclencher : le bouton **Dicter** de la fenêtre principale,
l'entrée **Dicter** du menu de la zone de notification, ou l'onglet **Fichiers**
pour transcrire de l'audio existant.

Fermer la fenêtre principale ne quitte pas : le raccourci reste actif.

## Transcrire des fichiers existants

Onglet **Fichiers** : glisse-dépose ou parcours. Audio et vidéo, n'importe quel
format — pour une vidéo, seule la piste son est extraite. Les formats courants
passent par `soundfile` ; le reste par `ffmpeg` (fourni dans `bin/`).

## Langue et anglicismes

Le réglage de langue est sur **Automatique** par défaut, et c'est délibéré :
forcer « français » pousse le décodeur à transcrire phonétiquement les mots
anglais réellement prononcés (« meeting » → « mitine »). L'amorce donnée à
Whisper contient d'ailleurs des anglicismes écrits en anglais, précisément pour
qu'il les laisse tels quels.

Contrepartie : sur un clip très court sans mots réels, la détection automatique
peut se tromper de langue. Si ça arrive, force la langue dans Réglages.

## Modèles

Quatre modèles sont proposés, avec des réglages optimaux câblés par modèle.
**Parakeet v3 est le défaut** : le plus rapide, le meilleur en français parmi
les rapides, et son architecture *transducer* peut émettre une sortie vide au
lieu de forcer un token — il n'invente pas de texte sur les silences, contrairement
à Whisper.

Parakeet détecte la langue seul et **on ne peut pas la forcer** — ce qui tombe
bien pour du discours qui mélange français et anglais. Si tu préfères pouvoir
verrouiller la langue, bascule sur `whisper-large-v3-turbo`.

> À ne pas faire : passer `language="fr"` à Parakeet. Le texte est rigoureusement
> identique et le temps de calcul est multiplié par 7,5. Vérifié, pas supposé.

> À ne pas faire non plus : pré-télécharger un dépôt entier avec
> `snapshot_download` pour afficher un pourcentage. onnx-asr et faster-whisper ne
> prennent qu'une variante de quantification sur les cinq publiées — le dépôt
> Parakeet pèse 3 Go là où 600 Mo suffisent. La progression est donc mesurée en
> observant le cache grossir, et la barre reste indéterminée plutôt que
> d'afficher un pourcentage faux.

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
    media.py             lecture de tout fichier audio/vidéo (soundfile, ffmpeg)
    download.py          progression de téléchargement par observation du cache
    service.py           orchestration micro → moteur → historique
    server.py            transport WebSocket (aucune logique métier)
    history.py           SQLite + recherche plein texte FTS5
bin/ffmpeg.exe           décodage des formats que soundfile ne gère pas
frontend/                application Tauri 2
  src/                   overlay + fenêtre principale (HTML/CSS/JS)
  src-tauri/src/lib.rs   raccourci global, zone de notification, service Python
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
.\.venv\Scripts\python.exe scripts\ws_check.py 5          # dictée micro réelle
.\.venv\Scripts\python.exe scripts\file_check.py <fichier> # import de fichiers
```

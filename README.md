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

## Dictée continue

Réglages ▸ *Mode de dictée* ▸ **Continu**. Le texte tombe alors phrase par
phrase pendant que tu parles, au lieu d'arriver d'un bloc à la fin. Avec
**Écrire au curseur**, chaque phrase est frappée dans l'application au premier
plan — comme la dictée de Windows.

Le découpage se fait sur les **frontières de phrase**, jamais sur une horloge.
Chaque morceau envoyé au moteur est un groupe de souffle complet, borné par du
silence : le modèle travaille dans les mêmes conditions qu'en différé. Sur une
dictée française de 58 s, le texte est celui de la passe unique, y compris là où
ça compte — « extrêmement riche, ambitieuse et intellectuellement stimulante »
sort entier.

Le prix à payer est réel : **plus de relecture**. En différé le texte s'affiche
et s'édite avant que tu t'en serves ; en continu il est déjà dans ton document.
C'est pourquoi le différé reste le défaut.

Deux règles tenues par le code plutôt que par la documentation :

- **Une hésitation n'est pas une fin de phrase.** En dessous de `MIN_COMMIT_S`
  de parole accumulée, un simple silence ne valide rien : on attend un vrai
  arrêt. Sans ce garde-fou, « c'est une vision du contenu / extrêmement riche »
  partait en deux morceaux et le fragment isolé ressortait « Extrême Maris ».
- **Une seule détection de langue par dictée**, figée sur la première phrase
  conséquente et seulement si la confiance dépasse 0,85. Détecter phrase par
  phrase fait dérailler les petits modèles : sur une dictée française, `small`
  rendait « Уплиток, мюрмюр » puis du roumain. Ce n'est pas forcer la langue,
  c'est retrouver la granularité du mode différé. Parakeet et Canary ne
  rapportent jamais de langue détectée : rien ne se fige pour eux.

### Ce qui change par rapport au différé

| | Différé | Continu |
| --- | --- | --- |
| Le texte arrive | d'un bloc, à la fin | phrase par phrase |
| Relecture avant usage | oui | non |
| Raccourci | maintenir ou bascule | bascule, toujours |
| Cran *sans carte graphique* | tous les modèles | `small` seulement, en auto |

Le raccourci passe en bascule de force, et ce n'est pas un détail : en mode
maintenu, `Ctrl` reste physiquement enfoncé pendant qu'on frappe le texte, et
l'application cible reçoit des `Ctrl+lettre` au lieu des caractères.

### Sur le cran sans carte graphique

Le mode continu y est confortable côté vitesse — `whisper-small-cpu` tient 16×
le temps réel à 4 fils, très au-dessus du nécessaire. C'est la **détection de
langue** qui lâche, pas le calcul :

| Modèle | Détection sur la 1re phrase | Résultat |
| --- | --- | --- |
| Whisper small | `fr` à 0,98 | français correct |
| Whisper base | `ro` à 0,73 | roumain, puis russe |

Sur `base` et `tiny`, **force la langue dans Réglages** en dictée continue. Le
même extrait passe alors de « Si am trebuit testi para kits » à « Et on prend
des tests par acquis » — inexact, mais du français.

> Garde-fou mesuré au passage : sur une phrase courte, `base` a rendu l'amorce
> de dictée mot pour mot (« Voici une note dictée, ponctuée normalement… ») au
> lieu de transcrire. C'est la régurgitation déjà connue sur `tiny`. Une phrase
> qui n'est qu'un extrait de l'amorce est désormais jetée, faute de quoi elle
> serait frappée telle quelle dans le document.

### Niveau d'enregistrement

Les phrases sont remontées à un niveau de parole normal avant d'atteindre le
moteur, et le détecteur de parole reçoit son propre gain. Ce n'est pas de la
coquetterie : sur un enregistrement à 0,022 de pic — un micro faible — Silero
hachait 6,5 s de parole en sept bribes, et Whisper, privé de contexte, inventait
« Voici une autre vidéo » là où il était dit « voir ce que ça donne ». Normalisé,
il rend « Voilà, c'est que ça donne ».

Le moteur reçoit toujours l'audio d'origine en mode différé : là, les vingt
secondes de contexte suffisent à rattraper le niveau.

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

Mais « détection automatique » ne veut pas dire « fidèle », et la déformation va
dans les deux sens. Sur une dictée française contenant un vrai « Wow », mesuré
phrase par phrase :

| Ce qui a été dit | « c'est quand même super rapide » | « Wow » |
| --- | --- | --- |
| Parakeet v3 | « **it's quite** super rapide » | Wow |
| Whisper large-v3-turbo | ✓ | « Waouh » |
| Whisper FR distil | ✓ | « Waouh » |
| Whisper large-v3 | ✓ | ✓ |
| Canary 1B v2 | ✓ | ✓ |

Parakeet ne préserve pas les anglicismes : sa détection bascule **mot à mot** et
remplace des mots français par leurs quasi-homophones anglais (« et » → « and »,
« riche » → « rich », « Tu touches à la croisée de plusieurs » → « You touch at
the croisée of other »). Les deux modèles haut de gamme sont les seuls à faire
les deux choses à la fois : garder l'anglais réellement prononcé **et** ne pas
angliciser le français.

Contrepartie : sur un clip très court sans mots réels, la détection automatique
peut se tromper de langue. Si ça arrive, force la langue dans Réglages.

## Modèles

Huit modèles, rangés dans l'interface en quatre crans d'un curseur
vitesse/qualité. Les réglages optimaux sont câblés par modèle.

| Cran | Modèle | Pour quoi |
| --- | --- | --- |
| **Sans carte graphique** | Whisper tiny / base / small (CPU) | Machine sans GPU |
| **Rapide** | Parakeet v3 | Français pur ou anglais pur, réponse immédiate |
| **Équilibré** | Whisper large-v3-turbo *(défaut)* | La dictée de tous les jours |
| **Équilibré** | Whisper FR distil dec16 | Accents non hexagonaux |
| **Qualité** | Whisper large-v3 | Ce qui compte vraiment |
| **Qualité** | Canary 1B v2 | Le plus exact, le plus lent |

**`whisper-large-v3-turbo` est le défaut** parce qu'il est à la fois plus rapide
et plus juste que Parakeet sur du français mêlé d'anglais — voir le tableau de
la section précédente. Parakeet garde sa place au cran *Rapide* pour son
architecture *transducer*, qui peut émettre une sortie vide au lieu de forcer un
token : il n'invente pas de texte sur les silences, contrairement à Whisper.

> À ne pas faire : passer `language="fr"` à Parakeet. Le texte est rigoureusement
> identique et le temps de calcul est multiplié par 7,5. Vérifié, pas supposé.

### Sans carte graphique

Le cran *Sans carte graphique* existe pour que Murmure reste utilisable sur une
machine ordinaire. Mesuré sur un extrait de 25 s de parole française mêlée
d'anglais, **processeur seul, bridé à 4 fils** :

| Modèle | Temps | Facteur temps réel | Ce qu'il donne |
| --- | --- | --- | --- |
| Whisper tiny | 0,9 s | 28× | Compréhensible, beaucoup d'erreurs de mots |
| Whisper base | 2,0 s | 12× | Phrases correctes, se trompe sur les noms propres |
| Whisper small | 4,8 s | 5× | Le meilleur des trois sur CPU |

Les trois gardent les anglicismes réellement prononcés.

Deux décisions à ne pas défaire dans ce cran :

- **`compute_type: "int8"`, jamais `int8_float16`.** Ce dernier est un type GPU ;
  sur processeur CTranslate2 le refuse et retombe en silence sur autre chose.
- **`cpu_threads` borné à 4.** Une dictée ne doit pas confisquer la machine. Et
  une charge AVX sur tous les cœurs tire un pic de consommation que toutes les
  alimentations n'encaissent pas — vérifié à nos dépens.

> À ne pas faire : donner l'amorce de dictée à `tiny`. Le modèle est trop petit
> pour la suivre : il la recopie puis part en boucle (« la question de la
> question de la question… »). D'où `initial_prompt: None` sur cette seule
> entrée. Avec l'amorce il produisait du texte inutilisable **et** était treize
> fois plus lent, la boucle multipliant les étapes de décodage.

Deux pistes évaluées puis **écartées**, pour ne pas les réexplorer :

- `whisper-large-v3-french-distil-dec2` : distiller le décodeur ne sert presque
  à rien sur processeur, où c'est l'encodeur qui coûte cher — et celui-ci reste
  l'encodeur large-v3 entier. 2,1× le temps réel pour une sortie sans
  ponctuation ni majuscules.
- `canary-180m-flash` : très rapide (12×) mais il **perd le début** de
  l'enregistrement, de façon reproductible. Rédhibitoire pour de la dictée.

> Piège Canary : contrairement à Parakeet, il n'a **aucun jeton de détection
> automatique**. Son prompt est câblé sur `<|en|>`/`<|en|>` aux positions 4 et 5
> (voir `onnx_asr/models/nemo.py`), si bien que sans langue source explicite il
> **traduit** au lieu de transcrire — du français dicté ressort en anglais. C'est
> pourquoi c'est le seul modèle du catalogue à porter `needs_language`, et
> pourquoi on lui passe `target_language` égal à `language` : c'est cette
> égalité, et rien d'autre, qui distingue transcrire de traduire.

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
    streaming.py         dictée continue : segmentation VAD, phrase par phrase
    media.py             lecture de tout fichier audio/vidéo (soundfile, ffmpeg)
    download.py          progression de téléchargement par observation du cache
    service.py           orchestration micro → moteur → historique
    server.py            transport WebSocket (aucune logique métier)
    history.py           SQLite + recherche plein texte FTS5
bin/ffmpeg.exe           décodage des formats que soundfile ne gère pas
frontend/                application Tauri 2
  src/                   overlay + fenêtre principale (HTML/CSS/JS)
  src-tauri/src/lib.rs   raccourci global, zone de notification, service Python
  src-tauri/src/inject.rs frappe au curseur (SendInput Unicode)
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

# Dictée continue : un wav rejoué comme s'il sortait du micro.
.\.venv\Scripts\python.exe scripts\stream_check.py <fichier.wav> [id_modele]
.\.venv\Scripts\python.exe scripts\stream_service_check.py <fichier.wav> [id_modele]
```

`stream_check` montre le découpage et le moment où chaque phrase tombe
(`--realtime` pour juger le ressenti). `stream_service_check` passe par le
Service entier et vérifie le contrat que voit le frontend : une suite de
`commit`, **un seul** `final`, et une seule entrée d'historique dont le texte
est exactement la concaténation des phrases. Il travaille dans un dossier
temporaire — l'historique réel n'est pas touché.

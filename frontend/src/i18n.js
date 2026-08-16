/// Traduction de l'interface. Anglais par defaut, francais au choix.
///
/// Le catalogue est un objet plat par langue plutot qu'un fichier de traduction
/// par langue : les deux versions d'une meme phrase se lisent l'une sous
/// l'autre, ce qui est la seule chose qui les empeche de diverger quand le
/// comportement decrit change.
///
/// Les cles `srv.*` traduisent ce que le service emet. Le backend n'envoie plus
/// de phrase toute faite mais un couple (cle, parametres) — il ignore la langue
/// de l'interface, et c'est tres bien ainsi : elle peut changer pendant qu'un
/// fichier d'une heure se transcrit. Le champ `message` reste transmis et sert
/// de repli si la cle est inconnue, cas d'un service plus recent que l'UI.

const STORAGE_KEY = "murmure.language.v1";

export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "fr", label: "Français" },
];

/// Anglais par defaut : c'est un projet public. La langue de transcription,
/// elle, n'a rien a voir avec celle-ci et reste reglee separement.
export const DEFAULT_LANGUAGE = "en";

const STRINGS = {
  en: {
    // --- barre laterale ---
    "nav.dictate": "Dictate",
    "nav.stop": "Stop",
    "nav.transcribing": "Transcribing…",
    "nav.files": "Files",
    "nav.models": "Models",
    "nav.history": "History",
    "nav.settings": "Settings",

    "status.connecting": "Connecting…",
    "status.online": "Service running",
    "status.offline": "Service offline",
    "status.loadingModel": "Loading model…",
    "status.loadingNamed": "Loading {label}…",
    "status.ready": "Ready · {device}",
    "status.downloading": "Downloading…",

    // --- historique ---
    "history.title": "History",
    "history.count_one": "{count} dictation",
    "history.count_other": "{count} dictations",
    "history.seconds": "{value} s of audio",
    "history.minutes": "{value} min of audio",
    "history.hours": "{hours} h of audio",
    "history.hoursMinutes": "{hours} h {minutes} min of audio",
    "history.audioFolder": "Audio folder",
    "history.search": "Search your dictations…",
    "history.emptySearch": "No dictation matches.",
    "history.empty": "No dictations yet. Press your hotkey to start.",
    "date.today": "Today {time}",
    "date.yesterday": "Yesterday {time}",
    "history.copy": "Copy",
    "history.pin": "Pin",
    "history.unpin": "Unpin",
    "history.delete": "Delete",
    "history.timing": "{seconds} s → {latency} ms",

    "speaker.numbered": "Speaker {number}",
    "speaker.unknown": "Speaker ?",

    "player.play": "Play",
    "player.pause": "Pause",
    "player.playLabel": "Play the dictation from {date}",
    "player.pauseLabel": "Pause the dictation from {date}",
    "player.position": "Playback position",
    "player.rate": "Change playback speed",
    "player.rateLabel": "Playback speed: {rate}",
    "player.unavailable": "Audio unavailable",

    "dialog.deleteTitle": "Delete this dictation?",
    "dialog.deletePlain": "This entry will be removed from the history. This cannot be undone.",
    "dialog.deleteWithAudio":
      "This entry will be removed from the history. If Murmure recorded the audio, its local copy is deleted too.",
    "dialog.cancel": "Cancel",
    "dialog.confirmDelete": "Delete",

    // --- fichiers ---
    "files.title": "Transcribe files",
    "files.sub":
      "Audio or video, any format. Video files are fine: only the sound track is extracted.",
    "files.dropTitle": "Drop your files here",
    "files.browse": "Browse…",
    "files.formats": "Audio: {audio} — Video: {video}",
    "files.formatsNoVideo": "Audio: {audio} (ffmpeg missing: video unavailable)",
    "files.filterMedia": "Audio and video",
    "files.filterAll": "All files",
    "files.queued": "waiting…",
    "files.done": "{latency} ms · {factor}×",
    "files.doneSpeakers_one": "{latency} ms · {factor}× · {count} speaker",
    "files.doneSpeakers_other": "{latency} ms · {factor}× · {count} speakers",
    "files.finished_one": "{count} file processed",
    "files.finished_other": "{count} files processed",

    // --- modeles ---
    "models.title": "Models",
    "models.sub":
      "Drop any CTranslate2 or ONNX model into models/: it shows up here when the service restarts.",
    "models.tier.qualite": "Best quality",
    "models.tier.qualiteHint":
      "The most accurate. A few seconds more, worth it for recordings that matter.",
    "models.tier.equilibre": "Balanced",
    "models.tier.equilibreHint": "A little slower, clearly more reliable on difficult audio.",
    "models.tier.rapide": "Fast",
    "models.tier.rapideHint": "Near-instant answer, decent quality. For dictating as you go.",
    "models.tier.leger": "No graphics card",
    "models.tier.legerHint":
      "Runs on the processor alone, capped at 4 cores to keep the machine responsive.",
    "models.tier.custom": "Your models",
    "models.tier.customHint": "Dropped into models/.",
    "models.active": "● active",
    "models.loading": "loading…",
    "models.tagDefault": "recommended",
    "models.tagLocal": "local",
    "models.tagVram": "{value} MB VRAM",
    "models.tagCpu": "CPU only",
    "models.loadingToast": "Loading {label}…",
    "models.readyToast": "Model ready",

    // --- reglages ---
    "settings.title": "Settings",

    "settings.interface": "Interface",
    "settings.language": "Language",
    "settings.languageHint":
      "The language of this window. It has nothing to do with the language you speak — that is set under Transcription.",
    "settings.palette": "Palette",
    "settings.paletteHint": "Accent colours of the window and of the overlay.",
    "settings.paletteMagenta": "Magenta and blue",
    "settings.paletteRose": "Pink and violet",
    "settings.paletteViolet": "Violet and azure",
    "settings.density": "Background density",
    "settings.densityHint": "Higher: darker window, less of what is behind it showing through.",

    "settings.trigger": "Trigger",
    "settings.hotkey": "Global hotkey",
    "settings.mode": "Mode",
    "settings.modeHint": "Hold: you speak for as long as the key is down.",
    "settings.modeHold": "Hold the key",
    "settings.modeToggle": "Press once, press again",
    "settings.modeForced":
      "In continuous dictation the hotkey always works as press / press again.",

    "settings.dictation": "Dictation mode",
    "settings.whenText": "When the text arrives",
    "settings.whenTextHint":
      "Deferred: you speak, the text arrives in one piece, you read it before using it. Continuous: each sentence lands as you finish it — no proofreading step, but you see what the model understood while you speak.",
    "settings.modeDeferred": "Deferred — proofread first (recommended)",
    "settings.modeContinuous": "Continuous — sentence by sentence",
    "settings.injectCursor": "Type at the cursor",
    "settings.injectCursorHint":
      "Every settled sentence is typed into the foreground application. An application running as administrator will refuse the keystrokes.",
    "settings.phraseSilence": "End-of-sentence silence",
    "settings.phraseSilenceHint":
      "Too short and a hesitation splits the sentence in two, costing the model its context. Too long and the text keeps you waiting.",
    "settings.polish": "Polish at each pause",
    "settings.polishHint":
      "After every pause the last sentences are decoded together again: punctuation, capitals and repetitions fixed. Typing at the cursor waits for the polished text.",
    "settings.polishOn": "On — grouped re-decoding (recommended)",
    "settings.polishOff": "Off — sentence by sentence",
    "settings.preview": "Preview while dictating",
    "settings.previewHint":
      "Shows the sentence in progress in grey, without waiting for it to end. Never typed at the cursor. Inactive without a graphics card.",
    "settings.previewOff": "off",

    "settings.transcription": "Transcription",
    "settings.spokenLanguage": "Spoken language",
    "settings.spokenLanguageHint":
      "Automatic keeps borrowed words the way you pronounce them. Forcing a language pushes the model to respell them phonetically.",
    "settings.languageAuto": "Automatic (recommended)",
    "settings.languageFr": "French",
    "settings.languageEn": "English",
    // Formes courtes, pour la ligne de resume sous l'etat du service.
    "lang.auto": "Auto",
    "lang.fr": "French",
    "lang.en": "English",

    "settings.speakers": "Speaker identification",
    "settings.speakersIntro":
      "Splits an imported recording by who is speaking, and the transcript comes back as a dialogue. Imported files only: grouping voices means having heard the whole conversation, which cannot be done sentence by sentence while you dictate.",
    "settings.diarizeOn": "Identify speakers",
    "settings.diarizeOnHint": "For meetings and interviews; pointless if you are the only speaker.",
    "settings.diarizeOnMissing":
      "Unavailable: the diarization component is not installed. Run the service install again (uv pip install -e .).",
    "settings.diarizeOnDownload": "First use downloads {size} MB of models.",
    "settings.diarizeCount": "Number of people",
    "settings.diarizeCountHint":
      "If you know it, say so: the result is clearly more reliable than automatic detection.",
    "settings.diarizeAuto": "Detect automatically",
    "settings.diarizeCountN": "{count} people",
    "settings.diarizeThreshold": "Grouping sensitivity",
    "settings.diarizeThresholdHint":
      "Only used when the number of people is detected automatically. Lower: one voice gets split across several speakers. Higher: two voices get merged into one.",
    "settings.diarizeThresholdForced":
      "Unused: with the number of people set, the grouping follows that number.",
    "settings.diarizeModels": "Speaker models",
    "settings.diarizeModelsReady": "Installed. Nothing to download.",
    "settings.diarizeModelsMissing": "Not downloaded yet — {size} MB, fetched on first use.",
    "settings.diarizeModelsUnavailable": "Unavailable: sherpa-onnx is not installed.",
    "settings.diarizeDownloadNow": "Download now",
    "settings.diarizeRemove": "Remove",
    "settings.diarizeRemoved": "Speaker models removed",
    "settings.diarizeDownloaded": "Speaker models ready",

    "settings.mic": "Microphone",
    "settings.inputDevice": "Input device",
    "settings.defaultDevice": "Default device",
    "settings.preroll": "Pre-recording",
    "settings.prerollHint": "Captures what came before the key press, so the first word survives.",
    "settings.keepalive": "Microphone held open",
    "settings.keepaliveHint": "Avoids paying to open the device again between two dictations.",

    "settings.after": "After the dictation",
    "settings.clipboard": "Copy to the clipboard",
    "settings.reviewWindow": "Show the review window",
    "settings.reviewWindowHint":
      "Unchecked: the text is copied and the window disappears straight away.",
    "settings.keepAudio": "Keep the audio",
    "settings.keepAudioHint": "Stores the wav of each dictation beside its history entry.",
    "settings.trimPeriod": "Drop the final full stop",

    "settings.engine": "Engine",
    "settings.gpu": "Use the GPU",
    "settings.gpuHint": "Unchecked: everything runs on the CPU. Slower, but zero VRAM.",
    "settings.preload": "Preload at startup",
    "settings.preloadHint":
      "The model is loaded as soon as Murmure starts: the first dictation is instant.",

    // --- overlay ---
    "overlay.ready": "Ready",
    "overlay.listening": "Listening…",
    "overlay.streaming": "Listening — text arrives sentence by sentence",
    "overlay.lastPhrase": "Last sentence…",
    "overlay.transcribing": "Transcribing…",
    "overlay.done": "Done",
    "overlay.error": "Error",
    "overlay.offline": "Service offline",
    "overlay.phrases_one": "{count} sentence",
    "overlay.phrases_other": "{count} sentences",
    "overlay.speaking": "Sentence in progress…",
    "overlay.nothing": "Nothing to transcribe",
    "overlay.nothingReason": "Nothing to transcribe — {reason}",
    "overlay.notTyped": "Not typed — Murmure has the focus",
    "overlay.typingRefused": "The application refused the keystrokes",
    "overlay.copy": "Copy",
    "overlay.copied": "Copied ✓",
    "overlay.redo": "Redo",
    "overlay.stopLabel": "Stop the dictation",
    "overlay.stopTitle": "Stop and transcribe",
    "overlay.closeLabel": "Close the dictation window",
    "overlay.closeTitle": "Close (Esc)",
    "overlay.preparing": "Preparing {label}…",
    "overlay.downloading": "Downloading the model — {size} MB",
    "overlay.loadingMemory": "Loading into memory…",
    "overlay.warmup": "Warming up the GPU…",

    // --- messages du service ---
    "srv.stage_loading": "Preparing {label}…",
    "srv.stage_warmup": "Warming up the GPU kernels…",
    "srv.stage_downloading": "Downloading {label}…",
    "srv.error_model_load": "Loading the model: {detail}",
    "srv.empty_too_short": "too short or silent",
    "srv.empty_no_speech": "no speech detected",
    "srv.file_reading": "Reading {name}…",
    "srv.file_transcribing": "Transcribing {name} ({minutes} min)…",
    "srv.file_no_speech": "No speech detected",
    "srv.diarize_running": "Identifying speakers — {name}…",
    "srv.diarize_download": "Downloading {part} — {done_mb} MB",
    "srv.diarize_downloadTotal": "Downloading {part} — {done_mb} / {total_mb} MB",
    "srv.part.segmentation": "segmentation",
    "srv.part.embedding": "voice fingerprint",
    "srv.error_diarize_unavailable": "Speaker identification unavailable: {detail}",
    "srv.error_diarize_failed": "Speaker identification failed: {detail}",
    "srv.diarize_not_installed":
      "Speaker identification needs sherpa-onnx. Run the service install again: uv pip install -e .",
    "srv.diarize_download_failed": "Could not download the {part} model: {detail}",
    "srv.diarize_archive_unreadable": "The segmentation archive could not be read: {detail}",
    "srv.diarize_models_missing": "Speaker models missing after download. Expected in {path}.",
    "srv.diarize_config_rejected":
      "sherpa-onnx refused the diarization setup (missing or unreadable model).",

    // --- erreurs du raccourci (cote Rust) ---
    "hotkey.invalid": "Invalid hotkey: « {accelerator} ». Expected format: Ctrl+Alt+D",
    "hotkey.taken":
      "« {accelerator} » is already used by another application ({detail}). Pick another one.",
    "hotkey.set": "Hotkey: {accelerator}",
    "toast.audioFolder": "Audio folder: {detail}",
    "toast.copied": "Copied to the clipboard",
    "toast.deleted": "Dictation deleted",
    "toast.audioUnplayable": "This audio cannot be played",

    "tray.tooltip": "Murmure — local dictation",
    "tray.quit": "Quit",
    "unit.mb": "MB",
  },

  fr: {
    // --- barre laterale ---
    "nav.dictate": "Dicter",
    "nav.stop": "Arrêter",
    "nav.transcribing": "Transcription…",
    "nav.files": "Fichiers",
    "nav.models": "Modèles",
    "nav.history": "Historique",
    "nav.settings": "Réglages",

    "status.connecting": "Connexion…",
    "status.online": "Service actif",
    "status.offline": "Service hors ligne",
    "status.loadingModel": "Chargement du modèle…",
    "status.loadingNamed": "Chargement {label}…",
    "status.ready": "Prêt · {device}",
    "status.downloading": "Téléchargement…",

    // --- historique ---
    "history.title": "Historique",
    "history.count_one": "{count} dictée",
    "history.count_other": "{count} dictées",
    "history.seconds": "{value} s d'audio",
    "history.minutes": "{value} min d'audio",
    "history.hours": "{hours} h d'audio",
    "history.hoursMinutes": "{hours} h {minutes} min d'audio",
    "history.audioFolder": "Dossier audio",
    "history.search": "Rechercher dans les dictées…",
    "history.emptySearch": "Aucune dictée ne correspond.",
    "history.empty": "Aucune dictée pour l'instant. Appuie sur ton raccourci pour commencer.",
    "date.today": "Aujourd'hui {time}",
    "date.yesterday": "Hier {time}",
    "history.copy": "Copier",
    "history.pin": "Épingler",
    "history.unpin": "Détacher",
    "history.delete": "Supprimer",
    "history.timing": "{seconds} s → {latency} ms",

    "speaker.numbered": "Locuteur {number}",
    "speaker.unknown": "Locuteur ?",

    "player.play": "Lire",
    "player.pause": "Pause",
    "player.playLabel": "Lire l'audio de la dictée du {date}",
    "player.pauseLabel": "Mettre en pause l'audio de la dictée du {date}",
    "player.position": "Position de lecture",
    "player.rate": "Changer la vitesse de lecture",
    "player.rateLabel": "Vitesse de lecture : {rate}",
    "player.unavailable": "Audio indisponible",

    "dialog.deleteTitle": "Supprimer cette dictée ?",
    "dialog.deletePlain": "Cette entrée sera retirée de l'historique. Cette action est définitive.",
    "dialog.deleteWithAudio":
      "L'entrée sera retirée de l'historique. Si l'audio a été enregistré par Murmure, sa copie locale sera également supprimée.",
    "dialog.cancel": "Annuler",
    "dialog.confirmDelete": "Supprimer",

    // --- fichiers ---
    "files.title": "Transcrire des fichiers",
    "files.sub":
      "Audio ou vidéo, n'importe quel format. Les vidéos sont acceptées : seule la piste son est extraite.",
    "files.dropTitle": "Dépose tes fichiers ici",
    "files.browse": "Parcourir…",
    "files.formats": "Audio : {audio} — Vidéo : {video}",
    "files.formatsNoVideo": "Audio : {audio} (ffmpeg absent : vidéos indisponibles)",
    "files.filterMedia": "Audio et vidéo",
    "files.filterAll": "Tous les fichiers",
    "files.queued": "en attente…",
    "files.done": "{latency} ms · {factor}×",
    "files.doneSpeakers_one": "{latency} ms · {factor}× · {count} locuteur",
    "files.doneSpeakers_other": "{latency} ms · {factor}× · {count} locuteurs",
    "files.finished_one": "{count} fichier traité",
    "files.finished_other": "{count} fichiers traités",

    // --- modeles ---
    "models.title": "Modèles",
    "models.sub":
      "Dépose n'importe quel modèle CTranslate2 ou ONNX dans models/ : il apparaîtra ici au redémarrage du service.",
    "models.tier.qualite": "Qualité maximale",
    "models.tier.qualiteHint":
      "Le plus exact. Quelques secondes de plus, à réserver aux enregistrements qui comptent.",
    "models.tier.equilibre": "Équilibré",
    "models.tier.equilibreHint": "Un peu plus lent, nettement plus fiable sur l'audio difficile.",
    "models.tier.rapide": "Rapide",
    "models.tier.rapideHint":
      "Réponse quasi immédiate, qualité correcte. Pour la dictée au fil de l'eau.",
    "models.tier.leger": "Sans carte graphique",
    "models.tier.legerHint":
      "Tourne sur le processeur seul, bridé à 4 cœurs pour laisser la machine réactive.",
    "models.tier.custom": "Tes modèles",
    "models.tier.customHint": "Déposés dans models/.",
    "models.active": "● actif",
    "models.loading": "chargement…",
    "models.tagDefault": "recommandé",
    "models.tagLocal": "local",
    "models.tagVram": "{value} Mo VRAM",
    "models.tagCpu": "processeur seul",
    "models.loadingToast": "Chargement de {label}…",
    "models.readyToast": "Modèle prêt",

    // --- reglages ---
    "settings.title": "Réglages",

    "settings.interface": "Interface",
    "settings.language": "Langue",
    "settings.languageHint":
      "La langue de cette fenêtre. Elle n'a rien à voir avec la langue que tu parles : celle-là se règle dans Transcription.",
    "settings.palette": "Palette",
    "settings.paletteHint": "Couleurs d'accent de la fenêtre et de l'overlay.",
    "settings.paletteMagenta": "Magenta et bleu",
    "settings.paletteRose": "Rose et violet",
    "settings.paletteViolet": "Violet et azur",
    "settings.density": "Densité du fond",
    "settings.densityHint": "Plus élevée : fond plus sombre et contenu arrière moins visible.",

    "settings.trigger": "Déclenchement",
    "settings.hotkey": "Raccourci global",
    "settings.mode": "Mode",
    "settings.modeHint": "Maintenir : parle tant que la touche est enfoncée.",
    "settings.modeHold": "Maintenir la touche",
    "settings.modeToggle": "Appuyer / ré-appuyer",
    "settings.modeForced":
      "En dictée continue, le raccourci fonctionne toujours en appuyer / ré-appuyer.",

    "settings.dictation": "Mode de dictée",
    "settings.whenText": "Quand le texte arrive",
    "settings.whenTextHint":
      "Différé : tu parles, le texte arrive d'un bloc, tu le relis avant de l'utiliser. Continu : chaque phrase tombe dès que tu la termines — plus de relecture, mais tu vois ce que le modèle a compris pendant que tu parles.",
    "settings.modeDeferred": "Différé — relecture (recommandé)",
    "settings.modeContinuous": "Continu — phrase par phrase",
    "settings.injectCursor": "Écrire au curseur",
    "settings.injectCursorHint":
      "Chaque phrase validée est frappée dans l'application au premier plan. Une application lancée en administrateur refusera la frappe.",
    "settings.phraseSilence": "Silence de fin de phrase",
    "settings.phraseSilenceHint":
      "Trop court, une hésitation coupe la phrase en deux et le modèle perd le contexte. Trop long, le texte se fait attendre.",
    "settings.polish": "Polissage à la pause",
    "settings.polishHint":
      "Après chaque pause, les dernières phrases sont re-décodées ensemble : ponctuation, majuscules et répétitions corrigées. L'écriture au curseur attend le texte poli.",
    "settings.polishOn": "Activé — re-décodage groupé (recommandé)",
    "settings.polishOff": "Désactivé — phrase par phrase",
    "settings.preview": "Aperçu pendant la dictée",
    "settings.previewHint":
      "Affiche en grisé la phrase en cours, sans attendre qu'elle se termine. Jamais écrit au curseur. Inactif sans carte graphique.",
    "settings.previewOff": "inactif",

    "settings.transcription": "Transcription",
    "settings.spokenLanguage": "Langue parlée",
    "settings.spokenLanguageHint":
      "Automatique préserve les anglicismes tels que tu les prononces. Forcer une langue pousse le modèle à les déformer phonétiquement.",
    "settings.languageAuto": "Automatique (recommandé)",
    "settings.languageFr": "Français",
    "settings.languageEn": "Anglais",
    "lang.auto": "Auto",
    "lang.fr": "Français",
    "lang.en": "Anglais",

    "settings.speakers": "Identification des locuteurs",
    "settings.speakersIntro":
      "Sépare un enregistrement importé par personne qui parle, et la transcription revient en dialogue. Fichiers importés uniquement : regrouper les voix demande d'avoir entendu toute la conversation, ce qui est impossible phrase par phrase pendant que tu dictes.",
    "settings.diarizeOn": "Identifier les locuteurs",
    "settings.diarizeOnHint":
      "Pour les réunions et les entretiens ; inutile si tu es seul à parler.",
    "settings.diarizeOnMissing":
      "Indisponible : le composant de diarisation n'est pas installé. Relance l'installation du service (uv pip install -e .).",
    "settings.diarizeOnDownload": "Premier usage : {size} Mo de modèles seront téléchargés.",
    "settings.diarizeCount": "Nombre de personnes",
    "settings.diarizeCountHint":
      "Si tu le connais, indique-le : le résultat est nettement plus fiable qu'en détection automatique.",
    "settings.diarizeAuto": "Détection automatique",
    "settings.diarizeCountN": "{count} personnes",
    "settings.diarizeThreshold": "Sensibilité du regroupement",
    "settings.diarizeThresholdHint":
      "Utilisé seulement quand le nombre de personnes est détecté automatiquement. Plus bas : une même voix se fractionne en plusieurs locuteurs. Plus haut : deux voix n'en font plus qu'une.",
    "settings.diarizeThresholdForced":
      "Inutilisé : le nombre de personnes étant imposé, le regroupement s'y tient.",
    "settings.diarizeModels": "Modèles de locuteurs",
    "settings.diarizeModelsReady": "Installés. Rien à télécharger.",
    "settings.diarizeModelsMissing":
      "Pas encore téléchargés — {size} Mo, récupérés au premier usage.",
    "settings.diarizeModelsUnavailable": "Indisponible : sherpa-onnx n'est pas installé.",
    "settings.diarizeDownloadNow": "Télécharger maintenant",
    "settings.diarizeRemove": "Supprimer",
    "settings.diarizeRemoved": "Modèles de locuteurs supprimés",
    "settings.diarizeDownloaded": "Modèles de locuteurs prêts",

    "settings.mic": "Micro",
    "settings.inputDevice": "Périphérique d'entrée",
    "settings.defaultDevice": "Périphérique par défaut",
    "settings.preroll": "Pré-enregistrement",
    "settings.prerollHint":
      "Capture ce qui précède l'appui, pour ne pas perdre le premier mot.",
    "settings.keepalive": "Micro maintenu ouvert",
    "settings.keepaliveHint":
      "Évite de repayer l'ouverture du périphérique entre deux dictées.",

    "settings.after": "Après la dictée",
    "settings.clipboard": "Copier dans le presse-papier",
    "settings.reviewWindow": "Afficher la fenêtre de relecture",
    "settings.reviewWindowHint":
      "Décoché : le texte est copié et la fenêtre disparaît aussitôt.",
    "settings.keepAudio": "Conserver l'audio",
    "settings.keepAudioHint": "Garde le wav de chaque dictée à côté de l'entrée d'historique.",
    "settings.trimPeriod": "Supprimer le point final",

    "settings.engine": "Moteur",
    "settings.gpu": "Utiliser le GPU",
    "settings.gpuHint": "Décoché : tout tourne sur le CPU. Plus lent, mais zéro VRAM.",
    "settings.preload": "Précharger au démarrage",
    "settings.preloadHint":
      "Le modèle est chargé dès le lancement : la première dictée est instantanée.",

    // --- overlay ---
    "overlay.ready": "Prêt",
    "overlay.listening": "Écoute…",
    "overlay.streaming": "Écoute — le texte arrive au fil des phrases",
    "overlay.lastPhrase": "Dernière phrase…",
    "overlay.transcribing": "Transcription…",
    "overlay.done": "Terminé",
    "overlay.error": "Erreur",
    "overlay.offline": "Service hors ligne",
    "overlay.phrases_one": "{count} phrase",
    "overlay.phrases_other": "{count} phrases",
    "overlay.speaking": "Phrase en cours…",
    "overlay.nothing": "Rien à transcrire",
    "overlay.nothingReason": "Rien à transcrire — {reason}",
    "overlay.notTyped": "Texte non inséré — Murmure a le focus",
    "overlay.typingRefused": "Insertion refusée par l'application",
    "overlay.copy": "Copier",
    "overlay.copied": "Copié ✓",
    "overlay.redo": "Refaire",
    "overlay.stopLabel": "Arrêter la dictée",
    "overlay.stopTitle": "Arrêter et transcrire",
    "overlay.closeLabel": "Fermer la fenêtre de dictée",
    "overlay.closeTitle": "Fermer (Échap)",
    "overlay.preparing": "Préparation {label}…",
    "overlay.downloading": "Téléchargement du modèle — {size} Mo",
    "overlay.loadingMemory": "Chargement en mémoire…",
    "overlay.warmup": "Préparation GPU…",

    // --- messages du service ---
    "srv.stage_loading": "Préparation de {label}…",
    "srv.stage_warmup": "Préparation des noyaux GPU…",
    "srv.stage_downloading": "Téléchargement de {label}…",
    "srv.error_model_load": "Chargement du modèle : {detail}",
    "srv.empty_too_short": "trop court ou silencieux",
    "srv.empty_no_speech": "aucune parole détectée",
    "srv.file_reading": "Lecture de {name}…",
    "srv.file_transcribing": "Transcription de {name} ({minutes} min)…",
    "srv.file_no_speech": "Aucune parole détectée",
    "srv.diarize_running": "Identification des locuteurs — {name}…",
    "srv.diarize_download": "Téléchargement {part} — {done_mb} Mo",
    "srv.diarize_downloadTotal": "Téléchargement {part} — {done_mb} / {total_mb} Mo",
    "srv.part.segmentation": "segmentation",
    "srv.part.embedding": "empreinte vocale",
    "srv.error_diarize_unavailable": "Identification des locuteurs indisponible : {detail}",
    "srv.error_diarize_failed": "Identification des locuteurs en échec : {detail}",
    "srv.diarize_not_installed":
      "L'identification des locuteurs a besoin de sherpa-onnx. Relance l'installation du service : uv pip install -e .",
    "srv.diarize_download_failed": "Téléchargement du modèle {part} impossible : {detail}",
    "srv.diarize_archive_unreadable": "Archive de segmentation illisible : {detail}",
    "srv.diarize_models_missing":
      "Modèles de locuteurs absents après téléchargement. Attendus dans {path}.",
    "srv.diarize_config_rejected":
      "sherpa-onnx a refusé la configuration de diarisation (modèle manquant ou illisible).",

    // --- erreurs du raccourci (cote Rust) ---
    "hotkey.invalid": "Raccourci invalide : « {accelerator} ». Format attendu : Ctrl+Alt+D",
    "hotkey.taken":
      "« {accelerator} » est déjà utilisé par une autre application ({detail}). Choisis-en un autre.",
    "hotkey.set": "Raccourci : {accelerator}",
    "toast.audioFolder": "Dossier audio : {detail}",
    "toast.copied": "Copié dans le presse-papier",
    "toast.deleted": "Dictée supprimée",
    "toast.audioUnplayable": "Impossible de lire cet audio",

    "tray.tooltip": "Murmure — dictée locale",
    "tray.quit": "Quitter",
    "unit.mb": "Mo",
  },
};

function normalize(code) {
  return STRINGS[code] ? code : DEFAULT_LANGUAGE;
}

let current = DEFAULT_LANGUAGE;
try {
  current = normalize(localStorage.getItem(STORAGE_KEY));
} catch {
  // Stockage refuse : on reste sur le defaut plutot que d'empecher le demarrage.
}

const listeners = new Set();

export function getLanguage() {
  return current;
}

/// Change la langue et previent les abonnes. Les deux fenetres partagent le
/// meme `localStorage` : l'evenement « storage » ci-dessous propage le choix a
/// l'overlay sans qu'aucune des deux n'ait a connaitre l'autre.
export function setLanguage(code) {
  const next = normalize(code);
  if (next === current) return current;
  current = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* la langue vivra le temps de la session */
  }
  notify();
  return current;
}

export function onLanguageChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify() {
  document.documentElement.lang = current;
  for (const fn of listeners) fn(current);
}

window.addEventListener("storage", (event) => {
  if (event.key !== STORAGE_KEY) return;
  const next = normalize(event.newValue);
  if (next === current) return;
  current = next;
  notify();
});

function interpolate(template, params) {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    params[name] === undefined || params[name] === null ? match : String(params[name]),
  );
}

/// Traduit une cle. Une cle inconnue est rendue telle quelle plutot que vide :
/// un libelle bizarre se voit et se corrige, une etiquette vide passe inapercue.
export function t(key, params) {
  const table = STRINGS[current] ?? STRINGS[DEFAULT_LANGUAGE];
  const template = table[key] ?? STRINGS[DEFAULT_LANGUAGE][key] ?? key;
  return interpolate(template, params);
}

/// Pluriel. Le francais met au singulier a zero comme a un, l'anglais non :
/// « 0 dictée » mais « 0 dictations ».
export function tn(key, count, params) {
  const singular = current === "fr" ? count <= 1 : count === 1;
  return t(`${key}_${singular ? "one" : "other"}`, { count, ...params });
}

const ATTRIBUTES = [
  ["data-i18n-title", "title"],
  ["data-i18n-placeholder", "placeholder"],
  ["data-i18n-label", "aria-label"],
];

/// Applique les traductions au balisage statique. Rappelable a volonte : les
/// cles restent dans les attributs, donc un changement de langue se contente de
/// repasser dessus, sans reconstruire le DOM ni perdre le focus courant.
export function translateDom(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const [attribute, target] of ATTRIBUTES) {
    for (const node of root.querySelectorAll(`[${attribute}]`)) {
      node.setAttribute(target, t(node.getAttribute(attribute)));
    }
  }
  document.documentElement.lang = current;
}

/// Traduit un evenement du service. `key` + `params` quand le service sait de
/// quoi il parle, `message` sinon — un service plus recent que l'interface peut
/// emettre une cle inconnue, et sa phrase toute faite vaut mieux que rien.
export function fromService(event, fallbackKey = "") {
  const key = event?.key || fallbackKey;
  const table = STRINGS[current] ?? STRINGS[DEFAULT_LANGUAGE];
  if (key && (table[`srv.${key}`] || STRINGS[DEFAULT_LANGUAGE][`srv.${key}`])) {
    const params = { ...(event?.params ?? {}) };
    // Le nom de la piece telechargee est lui-meme une cle.
    if (params.part) params.part = t(`srv.part.${params.part}`);
    if (key === "diarize_download" && params.total_mb) {
      return t("srv.diarize_downloadTotal", params);
    }
    return t(`srv.${key}`, params);
  }
  return event?.message ?? "";
}

document.documentElement.lang = current;

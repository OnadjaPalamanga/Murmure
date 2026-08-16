import { Bus, formatDate } from "./ws.js";
import { applyAppearance, loadAppearance, saveAppearance } from "./appearance.js";
import {
  LANGUAGES,
  fromService,
  getLanguage,
  onLanguageChange,
  setLanguage,
  t,
  tn,
  translateDom,
} from "./i18n.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { writeText } = window.__TAURI__.clipboardManager;

const bus = new Bus();
let snapshot = null;
let searchTimer = null;
// Dernier etat de dictee connu : le libelle du bouton en depend, et un
// changement de langue doit pouvoir le reconstruire sans attendre l'evenement
// suivant du service.
let dictationState = "idle";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ------------------------------------------------------------- apparence

let appearance = applyAppearance(loadAppearance());

function renderAppearance() {
  $$(".palette-swatch").forEach((swatch) => {
    swatch.setAttribute("aria-pressed", String(swatch.dataset.palette === appearance.palette));
  });
  $("#appearance-density").value = String(appearance.density);
  $("#out-appearance-density").textContent = `${appearance.density} %`;
}

function bindAppearance() {
  $$(".palette-swatch").forEach((swatch) => {
    swatch.addEventListener("click", () => {
      appearance = saveAppearance({ ...appearance, palette: swatch.dataset.palette });
      renderAppearance();
    });
  });

  $("#appearance-density").addEventListener("input", (event) => {
    appearance = saveAppearance({ ...appearance, density: Number(event.target.value) });
    renderAppearance();
  });

  renderAppearance();
}

// ------------------------------------------------------------- navigation

function showTab(tab) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === tab));
}

$$(".nav-item").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
listen("navigate", ({ payload }) => showTab(payload));

// ------------------------------------------------------------------ toast

let toastTimer = null;
function toast(message, ms = 1900) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), ms);
}

let pendingDelete = null;
const deleteDialog = $("#delete-dialog");

function requestDelete(entry) {
  pendingDelete = entry;
  $("#delete-message").textContent = entry.audio_path
    ? t("dialog.deleteWithAudio")
    : t("dialog.deletePlain");
  deleteDialog.showModal();
}

deleteDialog.addEventListener("close", () => {
  if (deleteDialog.returnValue === "confirm" && pendingDelete) {
    bus.send("history_delete", { id: pendingDelete.id });
  }
  pendingDelete = null;
});

// -------------------------------------------------------------- historique

function formatHistoryDuration(seconds) {
  const total = Math.max(0, Math.round(seconds ?? 0));
  if (total < 60) return t("history.seconds", { value: total });
  if (total < 3600) return t("history.minutes", { value: Math.round(total / 60) });
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  return minutes
    ? t("history.hoursMinutes", { hours, minutes })
    : t("history.hours", { hours });
}

function formatAudioTime(seconds) {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

const AUDIO_RATES = [1, 1.25, 1.5, 2];
let audioPlaybackRate = 1;

function formatAudioRate(rate) {
  const text = getLanguage() === "fr" ? String(rate).replace(".", ",") : String(rate);
  return `${text}×`;
}

function buildAudioPlayer(entry) {
  const player = document.createElement("div");
  player.className = "entry-player";
  player.dataset.playing = "false";

  const when = formatDate(entry.created_at);

  const audio = document.createElement("audio");
  audio.className = "entry-audio";
  audio.preload = "metadata";
  audio.src = `http://127.0.0.1:8756/audio/${encodeURIComponent(entry.id)}`;
  audio.playbackRate = audioPlaybackRate;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "audio-toggle";
  toggle.setAttribute("aria-label", t("player.playLabel", { date: when }));
  toggle.title = t("player.play");
  toggle.innerHTML = '<span class="audio-icon" aria-hidden="true"></span>';

  const progress = document.createElement("input");
  progress.type = "range";
  progress.className = "audio-progress";
  progress.min = "0";
  progress.max = "1000";
  progress.step = "1";
  progress.value = "0";
  progress.setAttribute("aria-label", t("player.position"));

  const time = document.createElement("output");
  time.className = "audio-time";

  const rate = document.createElement("button");
  rate.type = "button";
  rate.className = "audio-rate";
  rate.textContent = formatAudioRate(audioPlaybackRate);
  rate.title = t("player.rate");
  rate.setAttribute("aria-label", t("player.rateLabel", { rate: formatAudioRate(audioPlaybackRate) }));

  const duration = () =>
    Number.isFinite(audio.duration) && audio.duration > 0
      ? audio.duration
      : Number(entry.audio_seconds ?? 0);

  const renderPosition = () => {
    const total = duration();
    const ratio = total > 0 ? Math.min(1, audio.currentTime / total) : 0;
    progress.value = String(Math.round(ratio * 1000));
    progress.style.setProperty("--audio-position", `${(ratio * 100).toFixed(2)}%`);
    time.textContent = `${formatAudioTime(audio.currentTime)} / ${formatAudioTime(total)}`;
  };

  const renderPlayback = () => {
    const playing = !audio.paused && !audio.ended;
    player.dataset.playing = String(playing);
    toggle.title = playing ? t("player.pause") : t("player.play");
    toggle.setAttribute(
      "aria-label",
      playing ? t("player.pauseLabel", { date: when }) : t("player.playLabel", { date: when }),
    );
  };

  toggle.addEventListener("click", () => {
    if (audio.paused) {
      $$("audio.entry-audio").forEach((other) => {
        if (other !== audio) other.pause();
      });
      audio.play().catch(() => toast(t("toast.audioUnplayable"), 3500));
    } else {
      audio.pause();
    }
  });

  progress.addEventListener("input", () => {
    const total = duration();
    if (total > 0) audio.currentTime = (Number(progress.value) / 1000) * total;
    renderPosition();
  });

  rate.addEventListener("click", () => {
    const index = AUDIO_RATES.indexOf(audioPlaybackRate);
    audioPlaybackRate = AUDIO_RATES[(index + 1) % AUDIO_RATES.length];
    $$("audio.entry-audio").forEach((item) => {
      item.playbackRate = audioPlaybackRate;
    });
    $$(".audio-rate").forEach((item) => {
      item.textContent = formatAudioRate(audioPlaybackRate);
      item.setAttribute(
        "aria-label",
        t("player.rateLabel", { rate: formatAudioRate(audioPlaybackRate) }),
      );
    });
  });

  audio.addEventListener("loadedmetadata", renderPosition);
  audio.addEventListener("durationchange", renderPosition);
  audio.addEventListener("timeupdate", renderPosition);
  audio.addEventListener("play", renderPlayback);
  audio.addEventListener("pause", renderPlayback);
  audio.addEventListener("ended", () => {
    audio.currentTime = 0;
    renderPlayback();
    renderPosition();
  });
  audio.addEventListener("error", () => {
    toggle.disabled = true;
    player.dataset.error = "true";
    player.title = t("player.unavailable");
  });

  player.append(audio, toggle, progress, time, rate);
  renderPosition();
  return player;
}

/// Une dictée diarisée s'affiche en dialogue : un tour de parole par ligne,
/// avec le nom du locuteur. La couleur est dérivée de son numéro — deux
/// personnes qui se répondent doivent se distinguer d'un coup d'œil, sans avoir
/// à lire l'étiquette.
function buildDialogue(segments) {
  return segments
    .filter((s) => s.text?.trim())
    .map((s) => {
      const turn = document.createElement("p");
      turn.className = "turn";
      // Modulo sur la palette : au-delà, les couleurs se répètent plutôt que
      // de sortir de la charte. Au-delà de six locuteurs, l'étiquette porte
      // seule l'information.
      turn.dataset.speaker = s.speaker === null ? "unknown" : String(s.speaker % 6);

      const who = document.createElement("span");
      who.className = "turn-who";
      who.textContent =
        s.speaker === null ? t("speaker.unknown") : t("speaker.numbered", { number: s.speaker + 1 });
      who.title = `${formatAudioTime(s.start)} → ${formatAudioTime(s.end)}`;

      const said = document.createElement("span");
      said.className = "turn-text";
      said.textContent = s.text;

      turn.append(who, said);
      return turn;
    });
}

function renderHistoryStats(stats = {}) {
  const count = Number(stats.count ?? 0);
  $("#history-count").textContent = tn("history.count", count);
  $("#history-duration").textContent = formatHistoryDuration(stats.total_audio_seconds);
}

function renderHistory(entries) {
  const list = $("#history-list");
  list.textContent = "";

  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = $("#search").value.trim() ? t("history.emptySearch") : t("history.empty");
    list.appendChild(empty);
    return;
  }

  for (const entry of entries) {
    const card = document.createElement("article");
    card.className = "entry";

    // Construit noeud par noeud : l'identifiant d'un modele depose a la main
    // est un nom de dossier choisi par l'utilisateur, pas du HTML de confiance.
    const top = document.createElement("div");
    top.className = "entry-top";
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = formatDate(entry.created_at);
    const model = document.createElement("span");
    model.className = "pill";
    model.textContent = entry.model_id;
    const grow = document.createElement("span");
    grow.className = "grow";
    const timing = document.createElement("span");
    timing.textContent = t("history.timing", {
      seconds: entry.audio_seconds.toFixed(1),
      latency: entry.latency_ms,
    });
    top.append(when, model, grow, timing);

    const text = document.createElement("div");
    text.className = "entry-text";
    if (entry.segments?.length) {
      text.classList.add("dialogue");
      text.append(...buildDialogue(entry.segments));
    } else {
      text.textContent = entry.text;
    }
    text.addEventListener("click", () => card.classList.toggle("open"));

    const actions = document.createElement("div");
    actions.className = "entry-actions";

    const copy = document.createElement("button");
    copy.className = "mini";
    copy.textContent = t("history.copy");
    copy.addEventListener("click", async () => {
      await writeText(entry.text);
      toast(t("toast.copied"));
    });

    const pin = document.createElement("button");
    pin.className = "mini";
    pin.textContent = entry.pinned ? t("history.unpin") : t("history.pin");
    pin.addEventListener("click", () =>
      bus.send("history_pin", { id: entry.id, pinned: !entry.pinned }),
    );

    const del = document.createElement("button");
    del.className = "mini danger";
    del.textContent = t("history.delete");
    del.addEventListener("click", () => requestDelete(entry));

    actions.append(copy, pin, del);

    const footer = document.createElement("div");
    footer.className = "entry-footer";
    footer.appendChild(actions);
    if (entry.audio_path) {
      footer.appendChild(buildAudioPlayer(entry));
    }

    card.append(top, text, footer);
    list.appendChild(card);
  }
}

function refreshHistory() {
  bus.send("history_search", { query: $("#search").value, limit: 200 });
}

$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshHistory, 160);
});

// ------------------------------------------------------------------ modeles

// Le catalogue commence par les modeles les plus puissants, qui sont le choix
// naturel sur une machine equipee d'une carte graphique.
const TIERS = ["qualite", "equilibre", "rapide", "leger"];

function modelBlurb(model) {
  // Un modele depose a la main peut n'etre decrit que dans une langue : on
  // affiche ce qui existe plutot qu'une carte vide.
  return getLanguage() === "fr"
    ? model.blurb || model.blurb_en
    : model.blurb_en || model.blurb;
}

function modelLanguages(model) {
  return getLanguage() === "fr"
    ? model.languages || model.languages_en
    : model.languages_en || model.languages;
}

function buildModelCard(model, activeId, engine) {
  const card = document.createElement("button");
  card.className = "model" + (model.id === activeId ? " active" : "");

  const busy = model.id === activeId && engine.loading;
  const check = document.createElement("span");
  check.className = "check";
  check.textContent = busy ? t("models.loading") : t("models.active");

  const title = document.createElement("h3");
  title.textContent = model.label;

  const tags = document.createElement("div");
  tags.className = "tags";
  const addTag = (text, cls) => {
    const tag = document.createElement("span");
    tag.className = cls ? `tag ${cls}` : "tag";
    tag.textContent = text;
    tags.appendChild(tag);
  };
  if (model.is_default) addTag(t("models.tagDefault"), "reco");
  if (model.is_local) addTag(t("models.tagLocal"), "local");
  // Pas de VRAM annoncée = modèle du cran léger, qui tourne sur le processeur.
  if (model.vram_mb) addTag(t("models.tagVram", { value: model.vram_mb }));
  else addTag(t("models.tagCpu"));
  addTag(modelLanguages(model));

  const blurb = document.createElement("p");
  blurb.textContent = modelBlurb(model);

  card.append(check, title, tags, blurb);
  card.addEventListener("click", () => {
    if (model.id === activeId) return;
    bus.send("set_model", { model_id: model.id });
    toast(t("models.loadingToast", { label: model.label }));
  });
  return card;
}

function renderModels(models, activeId, engine) {
  const wrap = $("#model-cards");
  wrap.textContent = "";

  // Les modèles déposés à la main n'ont pas de niveau : ils vont dans leur
  // propre section plutôt que d'être rangés arbitrairement.
  const groups = [...TIERS.slice(0, -1), null, TIERS.at(-1)];

  for (const tier of groups) {
    const inTier = models.filter((m) => (tier === null ? !TIERS.includes(m.tier) : m.tier === tier));
    if (!inTier.length) continue;

    const head = document.createElement("div");
    head.className = "tier-head";

    const name = document.createElement("h2");
    name.textContent = tier === null ? t("models.tier.custom") : t(`models.tier.${tier}`);
    const hint = document.createElement("p");
    hint.textContent =
      tier === null ? t("models.tier.customHint") : t(`models.tier.${tier}Hint`);
    head.append(name, hint);
    wrap.appendChild(head);

    const row = document.createElement("div");
    row.className = "cards-row";
    for (const model of inTier) row.appendChild(buildModelCard(model, activeId, engine));
    wrap.appendChild(row);
  }
}

// ----------------------------------------------------------------- reglages

const FIELDS = [
  "hotkey",
  "hotkey_mode",
  "language",
  "input_device",
  "preroll_ms",
  "mic_keepalive_s",
  "copy_to_clipboard",
  "show_review_window",
  "keep_audio",
  "trim_trailing_period",
  "prefer_gpu",
  "preload_on_start",
  "dictation_mode",
  "inject_at_cursor",
  "phrase_silence_ms",
  "polish_mode",
  "preview_ms",
  "diarize_files",
  "diarize_speakers",
  "diarize_threshold",
];

const UNITS = {
  preroll_ms: "ms",
  mic_keepalive_s: "s",
  phrase_silence_ms: "ms",
  preview_ms: "ms",
};

/// Un meme reglage peut avoir plusieurs commandes : l'identification des
/// locuteurs se regle depuis Réglages, et se rappelle en haut de l'onglet
/// Fichiers, la ou on depose l'enregistrement. C'est `data-setting` et non
/// l'identifiant qui fait le lien, puisqu'un identifiant est unique par page.
const nodesFor = (name) => $$(`[data-setting="${name}"]`);
const nodeFor = (name) => nodesFor(name)[0];

/// « 0 ms » se lirait comme un reglage extreme alors que c'est un interrupteur.
function readout(name, value) {
  if (name === "preview_ms" && Number(value) === 0) return t("settings.previewOff");
  if (name === "diarize_threshold") {
    const text = Number(value).toFixed(2);
    return getLanguage() === "fr" ? text.replace(".", ",") : text;
  }
  return `${value} ${UNITS[name] ?? ""}`.trim();
}

function readNode(name, node) {
  if (node.type === "checkbox") return node.checked;
  if (node.type === "range") return Number(node.value);
  if (name === "input_device") return node.value === "" ? null : Number(node.value);
  // Le service attend un entier : « 0 » venu d'un <select> est une chaîne, et
  // une chaîne passerait le test « >= 2 » côté Python sans jamais l'être.
  if (name === "diarize_speakers") return Number(node.value);
  return node.value;
}

function writeField(name, value) {
  for (const node of nodesFor(name)) {
    if (node.type === "checkbox") node.checked = Boolean(value);
    else if (name === "input_device") node.value = value === null ? "" : String(value);
    else node.value = value ?? "";

    // Un reglage que le service ne renvoie pas — version plus ancienne que
    // l'interface — laisserait un menu vide, sans rien qui explique pourquoi.
    // On retombe sur sa premiere option, celle que le HTML declare par defaut.
    if (node.tagName === "SELECT" && node.selectedIndex < 0) node.selectedIndex = 0;
  }

  // La valeur RELUE depuis la commande, pas celle qu'on voulait y mettre. Le
  // fichier de reglages se modifie a la main : un `preroll_ms = 5000` hors des
  // bornes du curseur est ramene a 1000 par le navigateur, et afficher « 5000 »
  // a cote d'un curseur pousse a fond annoncerait un reglage qui n'existe pas.
  const out = document.getElementById(`out-${name}`);
  const node = nodeFor(name);
  if (out && node) out.textContent = readout(name, node.value);
}

/// Les reglages qui n'ont plus de sens en dictee continue sont grises plutot
/// que retires : voir pourquoi ils sont inactifs vaut mieux que les voir
/// disparaitre. « Maintenir la touche » notamment est force en bascule, sans
/// quoi le Ctrl reste enfonce pendant qu'on frappe le texte.
function reflectDictationMode() {
  const continuous = nodeFor("dictation_mode").value === "continu";

  const hotkeyMode = nodeFor("hotkey_mode");
  hotkeyMode.disabled = continuous;
  hotkeyMode.closest(".row").dataset.inactive = String(continuous);
  hotkeyMode.closest(".row").title = continuous ? t("settings.modeForced") : "";

  for (const name of ["inject_at_cursor", "phrase_silence_ms", "polish_mode", "preview_ms"]) {
    const node = nodeFor(name);
    node.disabled = !continuous;
    node.closest(".row").dataset.inactive = String(!continuous);
  }
}

// Vrai pendant un telechargement ou un effacement des modeles de locuteurs :
// les deux boutons restent inactifs tant que le service n'a pas repondu.
let diarizeBusy = false;

/// Etat de l'identification des locuteurs : ce qui est actif, ce qui ne l'est
/// pas, et pourquoi. Tout est grise plutot que retire — voir la raison vaut
/// mieux que voir un reglage disparaitre.
function reflectDiarization() {
  const info = snapshot?.diarization;
  const enabled = Boolean(nodeFor("diarize_files")?.checked);
  const forced = Number(nodeFor("diarize_speakers")?.value ?? 0) >= 2;

  for (const node of nodesFor("diarize_speakers")) {
    node.disabled = !enabled;
    node.closest(".row").dataset.inactive = String(!enabled);
  }

  // Le seuil ne sert qu'en detection automatique : sherpa-onnx l'ignore des
  // que le nombre de locuteurs est impose. Le laisser actif ferait croire a un
  // reglage sans effet.
  const threshold = nodeFor("diarize_threshold");
  threshold.disabled = !enabled || forced;
  threshold.closest(".row").dataset.inactive = String(!enabled || forced);
  $("#diarize-threshold-hint").textContent =
    enabled && forced ? t("settings.diarizeThresholdForced") : t("settings.diarizeThresholdHint");

  // Dire ce qui manque AVANT de lancer un fichier d'une heure, pas après.
  const size = info?.download_mb ?? 35;
  let hint = t("settings.diarizeOnHint");
  if (info && !info.available) hint = t("settings.diarizeOnMissing");
  else if (info && !info.models_ready) hint = t("settings.diarizeOnDownload", { size });
  for (const node of $$(".diarize-hint")) node.textContent = hint;

  const ready = Boolean(info?.models_ready);
  const available = info ? Boolean(info.available) : true;
  $("#diarize-models-hint").textContent = !available
    ? t("settings.diarizeModelsUnavailable")
    : ready
      ? t("settings.diarizeModelsReady")
      : t("settings.diarizeModelsMissing", { size });
  $("#btn-diarize-download").disabled = !available || ready || diarizeBusy;
  $("#btn-diarize-clear").disabled = !ready || diarizeBusy;
  $("#diarize-models-row").dataset.inactive = String(!available);
}

function bindDiarizationModels() {
  $("#btn-diarize-download").addEventListener("click", () => {
    diarizeBusy = true;
    reflectDiarization();
    // La reponse est un snapshot : `models_ready` y sera a jour, et c'est lui
    // qui rendra la main aux boutons.
    bus.send("diarize_download", {});
  });

  $("#btn-diarize-clear").addEventListener("click", () => {
    diarizeBusy = true;
    reflectDiarization();
    bus.send("diarize_clear", {});
    toast(t("settings.diarizeRemoved"));
  });
}

function bindSettings() {
  for (const name of FIELDS) {
    for (const node of nodesFor(name)) {
      const isText = node.tagName === "INPUT" && node.type === "text";

      // Les champs texte n'envoient qu'a la validation : sinon on enregistrerait
      // un raccourci incomplet a chaque frappe.
      node.addEventListener(isText ? "change" : "input", () => {
        const value = readNode(name, node);

        // Les commandes en double se suivent : changer l'une doit se voir sur
        // l'autre tout de suite, sinon l'onglet Fichiers et l'onglet Réglages
        // se contredisent jusqu'au prochain snapshot.
        writeField(name, value);
        if (snapshot?.settings) snapshot.settings[name] = value;
        bus.send("update_settings", { settings: { [name]: value } });

        if (name === "dictation_mode") reflectDictationMode();
        if (name === "diarize_files" || name === "diarize_speakers") reflectDiarization();

        if (name === "hotkey") {
          invoke("set_hotkey", { accelerator: value })
            .then(() => toast(t("hotkey.set", { accelerator: value })))
            .catch((err) => toast(hotkeyMessage(err)));
        }
      });
    }
  }
}

/// Le cote Rust rend un code et ses parametres, pas une phrase : c'est
/// l'interface qui sait dans quelle langue elle parle.
function hotkeyMessage(error) {
  if (error && typeof error === "object" && error.code) {
    return t(`hotkey.${error.code}`, error);
  }
  return String(error);
}

function renderDevices(devices, current) {
  const select = nodeFor("input_device");
  select.textContent = "";

  const auto = document.createElement("option");
  auto.value = "";
  auto.textContent = t("settings.defaultDevice");
  select.appendChild(auto);

  for (const device of devices) {
    const option = document.createElement("option");
    option.value = String(device.index);
    option.textContent = device.name;
    select.appendChild(option);
  }
  select.value = current === null || current === undefined ? "" : String(current);
}

/// Les libelles du nombre de locuteurs se declinent (« 3 personnes ») : ils
/// sont construits ici plutot qu'ecrits en dur dans le HTML, en deux langues.
function renderSpeakerOptions() {
  for (const select of $$(".speaker-count")) {
    const previous = select.value;
    select.textContent = "";

    const auto = document.createElement("option");
    auto.value = "0";
    auto.textContent = t("settings.diarizeAuto");
    select.appendChild(auto);

    for (let count = 2; count <= 8; count++) {
      const option = document.createElement("option");
      option.value = String(count);
      option.textContent = t("settings.diarizeCountN", { count });
      select.appendChild(option);
    }
    select.value = previous || "0";
    if (select.selectedIndex < 0) select.selectedIndex = 0;
  }
}

function renderLanguageOptions() {
  const select = $("#ui-language");
  select.textContent = "";
  for (const { code, label } of LANGUAGES) {
    const option = document.createElement("option");
    option.value = code;
    // Chaque langue est nommee dans sa propre langue : « Français » se
    // reconnait meme depuis une interface en anglais qu'on ne lit pas.
    option.textContent = label;
    select.appendChild(option);
  }
  select.value = getLanguage();
}

// ------------------------------------------------------------- progression

function showProgress(label, done, total) {
  const wrap = $("#progress");
  const bar = $("#progress-bar");
  wrap.hidden = false;
  $("#progress-label").textContent = label;

  if (total > 0) {
    bar.classList.remove("indeterminate");
    bar.style.width = `${Math.min(100, (done / total) * 100).toFixed(1)}%`;
  } else {
    // Total inconnu : une barre qui glisse plutôt qu'un pourcentage inventé.
    bar.classList.add("indeterminate");
  }
}

function hideProgress() {
  $("#progress").hidden = true;
  $("#progress-bar").classList.remove("indeterminate");
  $("#progress-bar").style.width = "0%";
}

const megabytes = (bytes) => `${(bytes / 1024 / 1024).toFixed(0)} ${t("unit.mb")}`;

function updateSidebarMeta(state = snapshot) {
  if (!state) return;
  const modelId = state.settings?.model_id ?? state.engine?.model_id;
  const model = state.models?.find((m) => m.id === modelId);
  // La langue parlee, pas celle de l'interface : c'est le reglage qui decide de
  // ce que le moteur entend, et il est utile de l'avoir toujours sous les yeux.
  const spoken = state.settings?.language ?? "auto";
  const known = ["auto", "fr", "en"].includes(spoken);
  $("#stats").textContent =
    `${model?.label ?? modelId ?? ""} · ${known ? t(`lang.${spoken}`) : spoken}`;
}

// ---------------------------------------------------------------- fichiers

let mediaExt = { audio: [], video: [] };
const jobs = new Map();

function renderJobs() {
  const wrap = $("#jobs");
  wrap.textContent = "";
  for (const [name, job] of [...jobs].reverse()) {
    const row = document.createElement("div");
    row.className = `job ${job.status}`;

    if (job.status === "run") {
      const spin = document.createElement("span");
      spin.className = "spin";
      row.appendChild(spin);
    }

    const label = document.createElement("span");
    label.className = "name";
    label.textContent = name;

    const state = document.createElement("span");
    state.className = "state";
    // Le libelle est recalcule ici : un lot en cours doit suivre la langue si
    // elle change au milieu.
    state.textContent = job.render ? job.render() : job.message;

    row.append(label, state);
    wrap.appendChild(row);
  }
}

function renderDropFormats() {
  $("#drop-formats").textContent = snapshot?.has_ffmpeg
    ? t("files.formats", { audio: mediaExt.audio.join(", "), video: mediaExt.video.join(", ") })
    : t("files.formatsNoVideo", { audio: mediaExt.audio.join(", ") });
}

function submitFiles(paths) {
  if (!paths?.length) return;
  for (const p of paths) {
    jobs.set(p.split(/[\\/]/).pop(), { status: "run", render: () => t("files.queued") });
  }
  renderJobs();
  showTab("files");
  bus.send("transcribe_files", { paths });
}

$("#btn-pick").addEventListener("click", async () => {
  const { open } = window.__TAURI__.dialog;
  const selected = await open({
    multiple: true,
    filters: [
      { name: t("files.filterMedia"), extensions: [...mediaExt.audio, ...mediaExt.video] },
      { name: t("files.filterAll"), extensions: ["*"] },
    ],
  });
  submitFiles(Array.isArray(selected) ? selected : selected ? [selected] : []);
});

$("#btn-audio-folder").addEventListener("click", () => {
  invoke("open_audio_folder").catch((err) => toast(t("toast.audioFolder", { detail: err }), 5000));
});

// Glisser-deposer : Tauri fournit les vrais chemins disque, contrairement a
// l'API fichier du navigateur.
const drop = $("#drop");
listen("tauri://drag-enter", () => drop.classList.add("over"));
listen("tauri://drag-leave", () => drop.classList.remove("over"));
listen("tauri://drag-drop", ({ payload }) => {
  drop.classList.remove("over");
  submitFiles(payload?.paths ?? []);
});

// ------------------------------------------------------------------- flux

bus.on("connection", ({ connected }) => {
  $("#status-dot").className = `dot ${connected ? "on" : "off"}`;
  $("#status-text").textContent = connected ? t("status.online") : t("status.offline");
  if (connected) refreshHistory();
});

bus.on("snapshot", (msg) => {
  snapshot = msg;
  diarizeBusy = false;
  renderModels(msg.models, msg.settings.model_id, msg.engine);
  renderDevices(msg.devices, msg.settings.input_device);
  for (const name of FIELDS) writeField(name, msg.settings[name]);
  reflectDictationMode();
  reflectDiarization();

  $("#cta-hotkey").textContent = msg.settings.hotkey;

  mediaExt = msg.media_extensions ?? mediaExt;
  renderDropFormats();

  updateSidebarMeta(msg);
  renderHistoryStats(msg.stats);
  updateEngineStatus(msg.engine);
  refreshHistory();
});

function updateEngineStatus(engine) {
  const dot = $("#status-dot");
  const text = $("#status-text");
  if (engine.loading) {
    dot.className = "dot busy";
    text.textContent = t("status.loadingModel");
  } else if (engine.loaded) {
    dot.className = "dot on";
    text.textContent = t("status.ready", { device: engine.device.toUpperCase() });
  } else {
    dot.className = "dot on";
    text.textContent = t("status.online");
  }
}

bus.on("history", ({ entries, stats }) => {
  renderHistory(entries);
  renderHistoryStats(stats);
});
bus.on("history_updated", refreshHistory);
bus.on("history_deleted", ({ stats }) => {
  renderHistoryStats(stats);
  refreshHistory();
  toast(t("toast.deleted"));
});

bus.on("model_loading", ({ label }) => {
  $("#status-dot").className = "dot busy";
  $("#status-text").textContent = t("status.loadingNamed", { label });
  showProgress(t("srv.stage_loading", { label }), 0, 0);
});

// Chaque étape est nommée : plus rien ne se passe à l'aveugle.
bus.on("model_stage", (msg) => {
  if (msg.stage === "downloading") {
    const { downloaded_bytes: done = 0, total_bytes: total = 0 } = msg;
    // Total inconnu : on affiche les octets reçus, jamais un pourcentage faux.
    showProgress(`${fromService(msg)} ${megabytes(done)}`, done, total);
    $("#status-text").textContent = t("status.downloading");
  } else {
    showProgress(fromService(msg), 0, 0);
  }
});

// Telechargement des modeles de locuteurs demande depuis les reglages.
bus.on("diarization_progress", (msg) => showProgress(fromService(msg), 0, 0));

// Emis reussite comme echec : c'est ce qui garantit que la barre se referme
// et que les boutons reprennent la main, meme quand rien n'a ete telecharge.
bus.on("diarization_done", ({ ready }) => {
  hideProgress();
  diarizeBusy = false;
  if (snapshot?.diarization) snapshot.diarization.models_ready = Boolean(ready);
  reflectDiarization();
  if (ready) toast(t("settings.diarizeDownloaded"));
});

bus.on("model_ready", (engine) => {
  hideProgress();
  updateEngineStatus(engine);
  if (snapshot) {
    snapshot.engine = engine;
    snapshot.settings.model_id = engine.model_id;
    renderModels(snapshot.models, engine.model_id, engine);
    updateSidebarMeta(snapshot);
  }
  toast(t("models.readyToast"));
});

bus.on("engine", (engine) => {
  updateEngineStatus(engine);
  if (snapshot) {
    snapshot.engine = engine;
    snapshot.settings.model_id = engine.model_id;
    renderModels(snapshot.models, engine.model_id, engine);
    updateSidebarMeta(snapshot);
  }
});

// --- dictée depuis la fenêtre principale ---

$("#btn-dictate").addEventListener("click", () => invoke("trigger_dictation"));

function renderDictationState() {
  $("#btn-dictate").classList.toggle("recording", dictationState === "recording");
  $("#cta-label").textContent =
    dictationState === "recording"
      ? t("nav.stop")
      : dictationState === "transcribing"
        ? t("nav.transcribing")
        : t("nav.dictate");
}

bus.on("state", ({ state }) => {
  dictationState = state;
  renderDictationState();
});

// --- lot de fichiers ---

bus.on("file_progress", (msg) => {
  jobs.set(msg.name, { status: "run", render: () => fromService(msg) });
  renderJobs();
  showProgress(`${fromService(msg)} (${msg.index}/${msg.total})`, msg.index - 1, msg.total);
});

bus.on("file_done", (msg) => {
  const params = { latency: msg.latency_ms, factor: msg.realtime_factor };
  const render = msg.ok
    ? msg.speakers
      ? () => tn("files.doneSpeakers", msg.speakers, params)
      : () => t("files.done", params)
    : () => fromService(msg);
  jobs.set(msg.name, { status: msg.ok ? "ok" : "ko", render });
  renderJobs();
  if (msg.ok) refreshHistory();
});

bus.on("files_finished", ({ total }) => {
  hideProgress();
  toast(tn("files.finished", total));
});

bus.on("final", () => refreshHistory());
bus.on("error", (msg) => {
  hideProgress();
  diarizeBusy = false;
  reflectDiarization();
  toast(fromService(msg), 6000);
});

// ------------------------------------------------------------------ langue

/// Un changement de langue repasse sur tout ce qui est affiche. Le DOM statique
/// garde ses cles dans les attributs, donc il suffit de le retraduire ; le reste
/// est reconstruit a partir du dernier snapshot, sans rien redemander au service.
function applyLanguage() {
  translateDom();
  renderLanguageOptions();
  renderSpeakerOptions();

  if (snapshot) {
    renderModels(snapshot.models, snapshot.settings.model_id, snapshot.engine);
    renderDevices(snapshot.devices, snapshot.settings.input_device);
    for (const name of FIELDS) writeField(name, snapshot.settings[name]);
    updateSidebarMeta(snapshot);
    renderHistoryStats(snapshot.stats);
    updateEngineStatus(snapshot.engine);
    renderDropFormats();
    refreshHistory();
  }

  reflectDictationMode();
  reflectDiarization();
  renderJobs();
  renderDictationState();
  syncTrayLanguage();
}

/// Le menu de la zone de notification vit cote Rust : il ne peut pas lire le
/// catalogue, on lui envoie donc les libelles traduits.
///
/// La page se charge pendant que l'application finit de se mettre en place : au
/// tout premier appel, le menu peut ne pas exister encore. Le cote Rust le dit
/// plutot que d'echouer, et on repasse — sinon un utilisateur francophone
/// garderait un menu anglais jusqu'au prochain changement de langue.
function syncTrayLanguage(attempt = 0) {
  invoke("set_tray_labels", {
    labels: {
      dictate: t("nav.dictate"),
      history: t("nav.history"),
      settings: t("nav.settings"),
      quit: t("tray.quit"),
      tooltip: t("tray.tooltip"),
    },
  })
    .then((applied) => {
      if (!applied && attempt < 5) setTimeout(() => syncTrayLanguage(attempt + 1), 400);
    })
    .catch(() => {
      /* le menu garde ses libelles precedents, ce n'est pas une raison d'alerter */
    });
}

$("#ui-language").addEventListener("change", (event) => setLanguage(event.target.value));
onLanguageChange(applyLanguage);

// ------------------------------------------------------------------ demarrage

renderLanguageOptions();
renderSpeakerOptions();
translateDom();
bindAppearance();
bindSettings();
bindDiarizationModels();
renderDictationState();
renderHistoryStats();
syncTrayLanguage();

// Un raccourci refuse (deja pris par une autre application) doit se voir :
// sinon l'utilisateur appuie dans le vide sans comprendre pourquoi.
invoke("hotkey_status").then((error) => {
  if (!error) return;
  showTab("settings");
  nodeFor("hotkey").style.borderColor = "var(--danger)";
  toast(hotkeyMessage(error), 9000);
});

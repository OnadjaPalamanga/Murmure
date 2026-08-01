import { Bus, formatDate } from "./ws.js";
import { applyAppearance, loadAppearance, saveAppearance } from "./appearance.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { writeText } = window.__TAURI__.clipboardManager;

const bus = new Bus();
let snapshot = null;
let searchTimer = null;

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
    ? "L'entrée sera retirée de l'historique. Si l'audio a été enregistré par Murmure, sa copie locale sera également supprimée."
    : "Cette entrée sera retirée de l'historique. Cette action est définitive.";
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
  if (total < 60) return `${total} s d'audio`;
  if (total < 3600) return `${Math.round(total / 60)} min d'audio`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  return `${hours} h${minutes ? ` ${minutes} min` : ""} d'audio`;
}

function formatAudioTime(seconds) {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

const AUDIO_RATES = [1, 1.25, 1.5, 2];
let audioPlaybackRate = 1;

function formatAudioRate(rate) {
  return `${String(rate).replace(".", ",")}×`;
}

function buildAudioPlayer(entry) {
  const player = document.createElement("div");
  player.className = "entry-player";
  player.dataset.playing = "false";

  const audio = document.createElement("audio");
  audio.className = "entry-audio";
  audio.preload = "metadata";
  audio.src = `http://127.0.0.1:8756/audio/${encodeURIComponent(entry.id)}`;
  audio.playbackRate = audioPlaybackRate;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "audio-toggle";
  toggle.setAttribute("aria-label", `Lire l'audio de la dictée du ${formatDate(entry.created_at)}`);
  toggle.title = "Lire";
  toggle.innerHTML = '<span class="audio-icon" aria-hidden="true"></span>';

  const progress = document.createElement("input");
  progress.type = "range";
  progress.className = "audio-progress";
  progress.min = "0";
  progress.max = "1000";
  progress.step = "1";
  progress.value = "0";
  progress.setAttribute("aria-label", "Position de lecture");

  const time = document.createElement("output");
  time.className = "audio-time";

  const rate = document.createElement("button");
  rate.type = "button";
  rate.className = "audio-rate";
  rate.textContent = formatAudioRate(audioPlaybackRate);
  rate.title = "Changer la vitesse de lecture";
  rate.setAttribute("aria-label", `Vitesse de lecture : ${formatAudioRate(audioPlaybackRate)}`);

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
    toggle.title = playing ? "Pause" : "Lire";
    toggle.setAttribute(
      "aria-label",
      `${playing ? "Mettre en pause" : "Lire"} l'audio de la dictée du ${formatDate(entry.created_at)}`
    );
  };

  toggle.addEventListener("click", () => {
    if (audio.paused) {
      $$("audio.entry-audio").forEach((other) => {
        if (other !== audio) other.pause();
      });
      audio.play().catch(() => toast("Impossible de lire cet audio", 3500));
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
      item.setAttribute("aria-label", `Vitesse de lecture : ${formatAudioRate(audioPlaybackRate)}`);
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
    player.title = "Audio indisponible";
  });

  player.append(audio, toggle, progress, time, rate);
  renderPosition();
  return player;
}

function renderHistoryStats(stats = {}) {
  const count = Number(stats.count ?? 0);
  $("#history-count").textContent = `${count} dictée${count === 1 ? "" : "s"}`;
  $("#history-duration").textContent = formatHistoryDuration(stats.total_audio_seconds);
}

function renderHistory(entries) {
  const list = $("#history-list");
  list.textContent = "";

  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = $("#search").value.trim()
      ? "Aucune dictée ne correspond."
      : "Aucune dictée pour l'instant. Appuie sur ton raccourci pour commencer.";
    list.appendChild(empty);
    return;
  }

  for (const entry of entries) {
    const card = document.createElement("article");
    card.className = "entry";

    const top = document.createElement("div");
    top.className = "entry-top";
    top.innerHTML = `
      <span class="when">${formatDate(entry.created_at)}</span>
      <span class="pill">${entry.model_id}</span>
      <span class="grow"></span>
      <span>${entry.audio_seconds.toFixed(1)} s → ${entry.latency_ms} ms</span>`;

    const text = document.createElement("div");
    text.className = "entry-text";
    text.textContent = entry.text;
    text.addEventListener("click", () => card.classList.toggle("open"));

    const actions = document.createElement("div");
    actions.className = "entry-actions";

    const copy = document.createElement("button");
    copy.className = "mini";
    copy.textContent = "Copier";
    copy.addEventListener("click", async () => {
      await writeText(entry.text);
      toast("Copié dans le presse-papier");
    });

    const pin = document.createElement("button");
    pin.className = "mini";
    pin.textContent = entry.pinned ? "Détacher" : "Épingler";
    pin.addEventListener("click", () =>
      bus.send("history_pin", { id: entry.id, pinned: !entry.pinned })
    );

    const del = document.createElement("button");
    del.className = "mini danger";
    del.textContent = "Supprimer";
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
const TIERS = [
  {
    id: "qualite",
    name: "Qualité maximale",
    hint: "Le plus exact. Quelques secondes de plus, à réserver aux enregistrements qui comptent.",
  },
  {
    id: "equilibre",
    name: "Équilibré",
    hint: "Un peu plus lent, nettement plus fiable sur l'audio difficile.",
  },
  {
    id: "rapide",
    name: "Rapide",
    hint: "Réponse quasi immédiate, qualité correcte. Pour la dictée au fil de l'eau.",
  },
  {
    id: "leger",
    name: "Sans carte graphique",
    hint: "Tourne sur le processeur seul, bridé à 4 cœurs pour laisser la machine réactive.",
  },
];

function buildModelCard(model, activeId, engine) {
  const card = document.createElement("button");
  card.className = "model" + (model.id === activeId ? " active" : "");

  const busy = model.id === activeId && engine.loading;
  const check = document.createElement("span");
  check.className = "check";
  check.textContent = busy ? "chargement…" : "● actif";

  const title = document.createElement("h3");
  title.textContent = model.label;

  const tags = document.createElement("div");
  tags.className = "tags";
  const addTag = (text, cls) => {
    const t = document.createElement("span");
    t.className = cls ? `tag ${cls}` : "tag";
    t.textContent = text;
    tags.appendChild(t);
  };
  if (model.is_default) addTag("recommandé", "reco");
  if (model.is_local) addTag("local", "local");
  // Pas de VRAM annoncée = modèle du cran léger, qui tourne sur le processeur.
  if (model.vram_mb) addTag(`${model.vram_mb} Mo VRAM`);
  else addTag("processeur seul");
  addTag(model.languages);

  const blurb = document.createElement("p");
  blurb.textContent = model.blurb;

  card.append(check, title, tags, blurb);
  card.addEventListener("click", () => {
    if (model.id === activeId) return;
    bus.send("set_model", { model_id: model.id });
    toast(`Chargement de ${model.label}…`);
  });
  return card;
}

function renderModels(models, activeId, engine) {
  const wrap = $("#model-cards");
  wrap.textContent = "";

  // Les modèles déposés à la main n'ont pas de niveau : ils vont dans leur
  // propre section plutôt que d'être rangés arbitrairement.
  const custom = { id: null, name: "Tes modèles", hint: "Déposés dans models/." };
  const groups = [...TIERS.slice(0, -1), custom, TIERS.at(-1)];

  for (const tier of groups) {
    const inTier = models.filter((m) =>
      tier.id === null ? !TIERS.some((t) => t.id === m.tier) : m.tier === tier.id,
    );
    if (!inTier.length) continue;

    const head = document.createElement("div");
    head.className = "tier-head";

    const name = document.createElement("h2");
    name.textContent = tier.name;
    const hint = document.createElement("p");
    hint.textContent = tier.hint;
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
];

const UNITS = { preroll_ms: "ms", mic_keepalive_s: "s", phrase_silence_ms: "ms" };

function readField(name) {
  const node = document.getElementById(`set-${name}`);
  if (node.type === "checkbox") return node.checked;
  if (node.type === "range") return Number(node.value);
  if (name === "input_device") return node.value === "" ? null : Number(node.value);
  return node.value;
}

function writeField(name, value) {
  const node = document.getElementById(`set-${name}`);
  if (node.type === "checkbox") node.checked = Boolean(value);
  else if (name === "input_device") node.value = value === null ? "" : String(value);
  else node.value = value ?? "";

  const out = document.getElementById(`out-${name}`);
  if (out) out.textContent = `${node.value} ${UNITS[name] ?? ""}`.trim();
}

/// Les reglages qui n'ont plus de sens en dictee continue sont grises plutot
/// que retires : voir pourquoi ils sont inactifs vaut mieux que les voir
/// disparaitre. « Maintenir la touche » notamment est force en bascule, sans
/// quoi le Ctrl reste enfonce pendant qu'on frappe le texte.
function reflectDictationMode() {
  const continuous = document.getElementById("set-dictation_mode").value === "continu";

  const hotkeyMode = document.getElementById("set-hotkey_mode");
  hotkeyMode.disabled = continuous;
  hotkeyMode.closest(".row").dataset.inactive = String(continuous);
  hotkeyMode.closest(".row").title = continuous
    ? "En dictée continue, le raccourci fonctionne toujours en appuyer / ré-appuyer."
    : "";

  for (const name of ["inject_at_cursor", "phrase_silence_ms"]) {
    const node = document.getElementById(`set-${name}`);
    node.disabled = !continuous;
    node.closest(".row").dataset.inactive = String(!continuous);
  }
}

function bindSettings() {
  for (const name of FIELDS) {
    const node = document.getElementById(`set-${name}`);
    const isText = node.tagName === "INPUT" && node.type === "text";

    // Les champs texte n'envoient qu'a la validation : sinon on enregistrerait
    // un raccourci incomplet a chaque frappe.
    node.addEventListener(isText ? "change" : "input", () => {
      const out = document.getElementById(`out-${name}`);
      if (out) out.textContent = `${node.value} ${UNITS[name] ?? ""}`.trim();

      const value = readField(name);
      bus.send("update_settings", { settings: { [name]: value } });

      if (name === "dictation_mode") reflectDictationMode();

      if (name === "hotkey") {
        invoke("set_hotkey", { accelerator: value })
          .then(() => toast(`Raccourci : ${value}`))
          .catch((err) => toast(String(err)));
      }
    });
  }
}

function renderDevices(devices, current) {
  const select = $("#set-input_device");
  select.textContent = "";

  const auto = document.createElement("option");
  auto.value = "";
  auto.textContent = "Périphérique par défaut";
  select.appendChild(auto);

  for (const device of devices) {
    const option = document.createElement("option");
    option.value = String(device.index);
    option.textContent = device.name;
    select.appendChild(option);
  }
  select.value = current === null || current === undefined ? "" : String(current);
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

const mo = (bytes) => `${(bytes / 1024 / 1024).toFixed(0)} Mo`;

const LANGUAGE_LABELS = {
  auto: "Auto",
  fr: "Français",
  en: "Anglais",
};

function updateSidebarMeta(state = snapshot) {
  if (!state) return;
  const modelId = state.settings?.model_id ?? state.engine?.model_id;
  const model = state.models?.find((m) => m.id === modelId);
  const language = LANGUAGE_LABELS[state.settings?.language] ?? state.settings?.language ?? "Auto";
  $("#stats").textContent = `${model?.label ?? modelId ?? "Modèle"} · ${language}`;
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
    state.textContent = job.message;

    row.append(label, state);
    wrap.appendChild(row);
  }
}

function submitFiles(paths) {
  if (!paths?.length) return;
  for (const p of paths) {
    jobs.set(p.split(/[\\/]/).pop(), { status: "run", message: "en attente…" });
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
      { name: "Audio et vidéo", extensions: [...mediaExt.audio, ...mediaExt.video] },
      { name: "Tous les fichiers", extensions: ["*"] },
    ],
  });
  submitFiles(Array.isArray(selected) ? selected : selected ? [selected] : []);
});

$("#btn-audio-folder").addEventListener("click", () => {
  invoke("open_audio_folder").catch((err) => toast(`Dossier audio : ${err}`, 5000));
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
  $("#status-text").textContent = connected ? "Service actif" : "Service hors ligne";
  if (connected) refreshHistory();
});

bus.on("snapshot", (msg) => {
  snapshot = msg;
  renderModels(msg.models, msg.settings.model_id, msg.engine);
  renderDevices(msg.devices, msg.settings.input_device);
  for (const name of FIELDS) writeField(name, msg.settings[name]);
  reflectDictationMode();

  $("#cta-hotkey").textContent = msg.settings.hotkey;

  mediaExt = msg.media_extensions ?? mediaExt;
  $("#drop-formats").textContent = msg.has_ffmpeg
    ? `Audio : ${mediaExt.audio.join(", ")} — Vidéo : ${mediaExt.video.join(", ")}`
    : `Audio : ${mediaExt.audio.join(", ")} (ffmpeg absent : vidéos indisponibles)`;

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
    text.textContent = "Chargement du modèle…";
  } else if (engine.loaded) {
    dot.className = "dot on";
    text.textContent = `Prêt · ${engine.device.toUpperCase()}`;
  } else {
    dot.className = "dot on";
    text.textContent = "Service actif";
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
  toast("Dictée supprimée");
});

bus.on("model_loading", ({ label }) => {
  $("#status-dot").className = "dot busy";
  $("#status-text").textContent = `Chargement ${label}…`;
  showProgress(`Préparation de ${label}…`, 0, 0);
});

// Chaque étape est nommée : plus rien ne se passe à l'aveugle.
bus.on("model_stage", (msg) => {
  if (msg.stage === "downloading") {
    const { downloaded_bytes: done = 0, total_bytes: total = 0 } = msg;
    // Total inconnu : on affiche les octets reçus, jamais un pourcentage faux.
    showProgress(`${msg.message} ${mo(done)} reçus`, done, total);
    $("#status-text").textContent = "Téléchargement…";
  } else {
    showProgress(msg.message, 0, 0);
  }
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
  toast("Modèle prêt");
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

bus.on("state", ({ state }) => {
  $("#btn-dictate").classList.toggle("recording", state === "recording");
  $("#cta-label").textContent =
    state === "recording" ? "Arrêter" : state === "transcribing" ? "Transcription…" : "Dicter";
});

// --- lot de fichiers ---

bus.on("file_progress", (msg) => {
  jobs.set(msg.name, { status: "run", message: msg.message });
  renderJobs();
  showProgress(`${msg.message} (${msg.index}/${msg.total})`, msg.index - 1, msg.total);
});

bus.on("file_done", (msg) => {
  jobs.set(msg.name, {
    status: msg.ok ? "ok" : "ko",
    message: msg.ok ? `${msg.latency_ms} ms · ${msg.realtime_factor}×` : msg.message,
  });
  renderJobs();
  if (msg.ok) refreshHistory();
});

bus.on("files_finished", ({ total }) => {
  hideProgress();
  toast(`${total} fichier${total > 1 ? "s" : ""} traité${total > 1 ? "s" : ""}`);
});

bus.on("final", () => refreshHistory());
bus.on("error", ({ message }) => {
  hideProgress();
  toast(message, 6000);
});

bindAppearance();
bindSettings();

// Un raccourci refuse (deja pris par une autre application) doit se voir :
// sinon l'utilisateur appuie dans le vide sans comprendre pourquoi.
invoke("hotkey_status").then((error) => {
  if (!error) return;
  showTab("settings");
  $("#set-hotkey").style.borderColor = "var(--danger)";
  toast(error, 9000);
});

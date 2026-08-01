import { Bus, formatDate } from "./ws.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { writeText } = window.__TAURI__.clipboardManager;

const bus = new Bus();
let snapshot = null;
let searchTimer = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

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

// -------------------------------------------------------------- historique

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
    del.addEventListener("click", () => bus.send("history_delete", { id: entry.id }));

    actions.append(copy, pin, del);
    card.append(top, text, actions);
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

function renderModels(models, activeId, engine) {
  const wrap = $("#model-cards");
  wrap.textContent = "";

  for (const model of models) {
    const card = document.createElement("button");
    card.className = "model" + (model.id === activeId ? " active" : "");

    const tags = [];
    if (model.is_default) tags.push(['<span class="tag reco">recommandé</span>']);
    if (model.is_local) tags.push(['<span class="tag local">local</span>']);
    if (model.vram_mb) tags.push([`<span class="tag">${model.vram_mb} Mo VRAM</span>`]);
    tags.push([`<span class="tag">${model.languages}</span>`]);

    const busy = model.id === activeId && engine.loading;
    card.innerHTML = `
      <span class="check">${busy ? "chargement…" : "● actif"}</span>
      <h3>${model.label}</h3>
      <div class="tags">${tags.join("")}</div>
      <p>${model.blurb}</p>`;

    card.addEventListener("click", () => {
      if (model.id === activeId) return;
      bus.send("set_model", { model_id: model.id });
      toast(`Chargement de ${model.label}…`);
    });

    wrap.appendChild(card);
  }
}

// ----------------------------------------------------------------- reglages

const FIELDS = [
  "hotkey",
  "hotkey_mode",
  "input_device",
  "preroll_ms",
  "mic_keepalive_s",
  "copy_to_clipboard",
  "show_review_window",
  "keep_audio",
  "trim_trailing_period",
  "prefer_gpu",
  "preload_on_start",
];

const UNITS = { preroll_ms: "ms", mic_keepalive_s: "s" };

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

  const { count, total_audio_seconds } = msg.stats;
  $("#stats").textContent = `${count} dictée${count > 1 ? "s" : ""} · ${Math.round(
    total_audio_seconds / 60
  )} min`;

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

bus.on("history", ({ entries }) => renderHistory(entries));
bus.on("history_updated", refreshHistory);
bus.on("history_deleted", () => {
  refreshHistory();
  toast("Dictée supprimée");
});

bus.on("model_loading", ({ label }) => {
  $("#status-dot").className = "dot busy";
  $("#status-text").textContent = `Chargement ${label}…`;
});

bus.on("model_ready", (engine) => {
  updateEngineStatus(engine);
  if (snapshot) renderModels(snapshot.models, engine.model_id, engine);
  toast("Modèle prêt");
});

bus.on("final", () => refreshHistory());
bus.on("error", ({ message }) => toast(message));

bindSettings();

// Un raccourci refuse (deja pris par une autre application) doit se voir :
// sinon l'utilisateur appuie dans le vide sans comprendre pourquoi.
invoke("hotkey_status").then((error) => {
  if (!error) return;
  showTab("settings");
  $("#set-hotkey").style.borderColor = "var(--danger)";
  toast(error, 9000);
});

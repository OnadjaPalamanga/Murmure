import { Bus, formatDuration } from "./ws.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { writeText } = window.__TAURI__.clipboardManager;

const BARS = 44;
const AUTO_HIDE_MS = 12000;

const el = {
  card: document.getElementById("card"),
  wave: document.getElementById("wave"),
  timer: document.getElementById("timer"),
  hint: document.getElementById("hint"),
  review: document.getElementById("review"),
  text: document.getElementById("text"),
  badge: document.getElementById("badge"),
  copy: document.getElementById("btn-copy"),
  redo: document.getElementById("btn-redo"),
};

// ---------------------------------------------------------------- etat

const bus = new Bus();
let settings = { hotkey_mode: "hold", copy_to_clipboard: true, show_review_window: true };
let state = "idle";
let recordingSince = 0;
let tickTimer = null;
let hideTimer = null;
let toggleActive = false;

// ------------------------------------------------------------ waveform

const bars = [];
for (let i = 0; i < BARS; i++) {
  const bar = document.createElement("i");
  bar.style.setProperty("--i", i);
  el.wave.appendChild(bar);
  bars.push(bar);
}

// Fenetre glissante des niveaux : la barre la plus a droite est la plus recente.
const levels = new Array(BARS).fill(0);

function pushLevel(value) {
  levels.push(value);
  levels.shift();
  for (let i = 0; i < BARS; i++) {
    // Racine carree : compresse la dynamique pour que la parole normale
    // occupe une belle amplitude visuelle au lieu de raser le bas.
    const h = 3 + Math.sqrt(levels[i]) * 46;
    bars[i].style.height = `${Math.min(h, 50).toFixed(1)}px`;
  }
}

function resetWave() {
  levels.fill(0);
  for (const bar of bars) bar.style.height = "3px";
}

// --------------------------------------------------------------- phases

function setState(next, hint) {
  state = next;
  el.card.dataset.state = next;
  if (hint !== undefined) el.hint.textContent = hint;
}

function startTicking() {
  recordingSince = Date.now();
  el.timer.textContent = "00:00";
  clearInterval(tickTimer);
  tickTimer = setInterval(() => {
    el.timer.textContent = formatDuration((Date.now() - recordingSince) / 1000);
  }, 200);
}

function stopTicking() {
  clearInterval(tickTimer);
  tickTimer = null;
}

function armHide(delay = AUTO_HIDE_MS) {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(dismiss, delay);
}

function dismiss() {
  clearTimeout(hideTimer);
  stopTicking();
  resetWave();
  setState("idle", "Prêt");
  el.text.textContent = "";
  el.badge.textContent = "";
  invoke("hide_overlay");
}

async function beginDictation() {
  clearTimeout(hideTimer);
  el.text.textContent = "";
  el.badge.textContent = "";
  resetWave();
  setState("recording", "Écoute…");
  startTicking();
  await invoke("show_overlay", { review: false });
  bus.send("start");
}

function endDictation() {
  if (state !== "recording") return;
  stopTicking();
  setState("transcribing", "Transcription…");
  bus.send("stop");
}

function abortDictation() {
  stopTicking();
  bus.send("cancel");
  dismiss();
}

// -------------------------------------------------------- raccourci global

listen("hotkey", async ({ payload }) => {
  const hold = settings.hotkey_mode === "hold";

  if (payload === "toggle" || !hold) {
    // Mode bascule : le premier appui demarre, le second termine.
    if (payload === "released") return;
    toggleActive = !toggleActive;
    if (toggleActive) await beginDictation();
    else endDictation();
    return;
  }

  if (payload === "pressed") {
    if (state !== "recording") await beginDictation();
  } else if (payload === "released") {
    endDictation();
  }
});

// -------------------------------------------------------- flux du service

bus.on("connection", ({ connected }) => {
  el.card.dataset.offline = String(!connected);
  if (!connected && state === "idle") el.hint.textContent = "Service hors ligne";
  else if (connected && state === "idle") el.hint.textContent = "Prêt";
});

bus.on("snapshot", (msg) => {
  settings = { ...settings, ...msg.settings };
});

bus.on("level", ({ value }) => {
  if (state === "recording") pushLevel(value);
});

bus.on("model_loading", ({ label }) => {
  if (state === "idle") el.hint.textContent = `Chargement ${label}…`;
});

bus.on("final", async (msg) => {
  const { entry, latency_ms, realtime_factor, device } = msg;
  stopTicking();
  setState("done", "Terminé");
  el.text.textContent = entry.text;
  el.badge.textContent = `${latency_ms} ms · ${realtime_factor}× · ${device}`;
  toggleActive = false;

  if (settings.copy_to_clipboard) {
    try {
      await writeText(entry.text);
      flashCopied();
    } catch {
      /* le presse-papier peut etre verrouille par une autre application */
    }
  }

  if (settings.show_review_window) {
    await invoke("show_overlay", { review: true });
    armHide();
  } else {
    dismiss();
  }
});

bus.on("empty", ({ reason }) => {
  stopTicking();
  toggleActive = false;
  setState("idle", reason ? `Rien à transcrire — ${reason}` : "Rien à transcrire");
  armHide(2200);
});

bus.on("error", ({ message }) => {
  stopTicking();
  toggleActive = false;
  setState("idle", "Erreur");
  el.hint.title = message;
  armHide(4500);
});

// ------------------------------------------------------------ interactions

function flashCopied() {
  el.copy.textContent = "Copié ✓";
  el.copy.classList.add("copied");
  setTimeout(() => {
    el.copy.textContent = "Copier";
    el.copy.classList.remove("copied");
  }, 1400);
}

el.copy.addEventListener("click", async () => {
  await writeText(el.text.textContent.trim());
  flashCopied();
  armHide(1800);
});

el.redo.addEventListener("click", beginDictation);

// Editer le texte suspend la fermeture automatique : on ne ferme pas
// une fenetre dans laquelle l'utilisateur est en train de taper.
el.text.addEventListener("input", () => clearTimeout(hideTimer));
el.text.addEventListener("blur", () => {
  if (state === "done") armHide();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (state === "recording") abortDictation();
    else dismiss();
  } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && state === "done") {
    el.copy.click();
  }
});

resetWave();

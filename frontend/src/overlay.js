import { Bus, formatDuration } from "./ws.js";
import { applyAppearance } from "./appearance.js";

applyAppearance();

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
  stop: document.getElementById("btn-stop"),
  close: document.getElementById("btn-close"),
};

// ---------------------------------------------------------------- etat

const bus = new Bus();
let settings = {
  hotkey_mode: "hold",
  copy_to_clipboard: true,
  show_review_window: true,
  dictation_mode: "differe",
  inject_at_cursor: false,
};
let state = "idle";
let recordingSince = 0;
let tickTimer = null;
let hideTimer = null;
let toggleActive = false;
// Evite que le resultat rouvre l'overlay si l'utilisateur l'a ferme pendant
// que la derniere transcription etait encore en cours.
let manuallyDismissed = false;
// Phrases deja frappees au curseur pendant la dictee en cours.
let streamed = [];
let injectionWarned = false;

const isContinuous = () => settings.dictation_mode === "continu";

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
  el.stop.disabled = next !== "recording" && next !== "streaming";
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
  streamed = [];
  invoke("hide_overlay");
}

async function beginDictation() {
  clearTimeout(hideTimer);
  manuallyDismissed = false;
  el.text.textContent = "";
  el.badge.textContent = "";
  streamed = [];
  injectionWarned = false;
  resetWave();
  startTicking();

  if (isContinuous()) {
    setState("streaming", "Écoute — le texte arrive au fil des phrases");
    // Haut pour montrer le texte qui s'accumule, mais sans prendre le focus :
    // le curseur doit rester la ou l'utilisateur ecrit.
    await invoke("show_overlay", { review: true, focus: false });
  } else {
    setState("recording", "Écoute…");
    await invoke("show_overlay", { review: false });
  }
  bus.send("start");
}

function endDictation() {
  if (state !== "recording" && state !== "streaming") return;
  stopTicking();
  toggleActive = false;
  setState("transcribing", isContinuous() ? "Dernière phrase…" : "Transcription…");
  bus.send("stop");
}

function abortDictation() {
  stopTicking();
  toggleActive = false;
  bus.send("cancel");
  dismiss();
}

function closeOverlay() {
  manuallyDismissed = true;
  if (state === "recording" || state === "streaming") abortDictation();
  else dismiss();
}

// ------------------------------------------------------ frappe au curseur

// Les frappes sont mises a la queue leu leu : deux phrases rapprochees
// s'entrelaceraient sinon, et le texte sortirait dans le desordre.
let injectChain = Promise.resolve();

function queueInjection(text, first) {
  injectChain = injectChain.then(() => injectPhrase(text, first)).catch(() => {});
  return injectChain;
}

async function injectPhrase(text, first) {
  // Les phrases se suivent dans un meme paragraphe : espace avant, sauf pour
  // la premiere, qui doit se coller a ce que l'utilisateur avait deja tape.
  const piece = first ? text : ` ${text}`;
  try {
    const typed = await invoke("type_text", { text: piece });
    if (!typed && !injectionWarned) {
      injectionWarned = true;
      el.hint.textContent = "Texte non inséré — Murmure a le focus";
    }
  } catch (err) {
    if (!injectionWarned) {
      injectionWarned = true;
      el.hint.textContent = "Insertion refusée par l'application";
      el.hint.title = String(err);
    }
  }
}

// -------------------------------------------------------- raccourci global

listen("hotkey", async ({ payload }) => {
  // En dictee continue on force la bascule. Maintenir la touche voudrait dire
  // garder Ctrl enfonce pendant qu'on frappe le texte : l'application cible
  // recevrait des Ctrl+lettre au lieu des caracteres. Et personne ne tient une
  // touche pendant cinq minutes.
  const hold = settings.hotkey_mode === "hold" && !isContinuous();

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
  if (state === "recording" || state === "streaming") pushLevel(value);
});

bus.on("model_loading", ({ label }) => {
  if (state !== "recording") el.hint.textContent = `Préparation ${label}…`;
});

// Une phrase vient d'etre transcrite : elle est definitive.
bus.on("commit", ({ text }) => {
  if (state !== "streaming") return;
  clearTimeout(hideTimer);

  streamed.push(text);
  if (settings.inject_at_cursor) queueInjection(text, streamed.length === 1);

  el.text.textContent = streamed.join(" ");
  el.text.scrollTop = el.text.scrollHeight;
  el.hint.textContent = `${streamed.length} phrase${streamed.length > 1 ? "s" : ""}`;
});

// Silero a entendu une attaque ou une fin : de quoi montrer qu'une phrase est
// en cours de formation, plutot que de laisser croire que rien ne se passe.
bus.on("speech", ({ speaking }) => {
  if (state !== "streaming" || !streamed.length) return;
  el.hint.textContent = speaking
    ? "Phrase en cours…"
    : `${streamed.length} phrase${streamed.length > 1 ? "s" : ""}`;
});

// L'overlay affiche aussi l'avancement : si on déclenche une dictée alors que le
// modèle se télécharge encore, il faut savoir pourquoi ça ne répond pas.
bus.on("model_stage", (msg) => {
  if (state === "recording" || state === "streaming") return;
  if (msg.stage === "downloading") {
    const mo = (msg.downloaded_bytes / 1024 / 1024).toFixed(0);
    el.hint.textContent = `Téléchargement du modèle — ${mo} Mo`;
  } else if (msg.stage === "loading") {
    el.hint.textContent = "Chargement en mémoire…";
  } else if (msg.stage === "warmup") {
    el.hint.textContent = "Préparation GPU…";
  }
});

bus.on("final", async (msg) => {
  const { entry, latency_ms, realtime_factor, device, streamed: wasStreamed } = msg;
  stopTicking();
  setState("done", "Terminé");
  el.text.textContent = entry.text;
  el.badge.textContent = `${latency_ms} ms · ${realtime_factor}× · ${device}`;
  toggleActive = false;

  // En continu, tout le texte est deja parti phrase par phrase — y compris la
  // derniere, que `finish()` transcrit avant d'emettre `final`. On ne rejoue
  // rien : on attend juste que la file de frappe se vide.
  if (wasStreamed) {
    await injectChain;
    streamed = [];
  }

  if (settings.copy_to_clipboard) {
    try {
      await writeText(entry.text);
      flashCopied();
    } catch {
      /* le presse-papier peut etre verrouille par une autre application */
    }
  }

  if (settings.show_review_window && !manuallyDismissed) {
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
el.stop.addEventListener("click", endDictation);
el.close.addEventListener("click", closeOverlay);

// Editer le texte suspend la fermeture automatique : on ne ferme pas
// une fenetre dans laquelle l'utilisateur est en train de taper.
el.text.addEventListener("input", () => clearTimeout(hideTimer));
el.text.addEventListener("blur", () => {
  if (state === "done") armHide();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeOverlay();
  } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && state === "done") {
    el.copy.click();
  }
});

resetWave();

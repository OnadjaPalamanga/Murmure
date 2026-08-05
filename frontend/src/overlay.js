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
  polish_mode: "moteur",
};
let state = "idle";
let recordingSince = 0;
let tickTimer = null;
let hideTimer = null;
let toggleActive = false;
// Evite que le resultat rouvre l'overlay si l'utilisateur l'a ferme pendant
// que la derniere transcription etait encore en cours.
let manuallyDismissed = false;
// Le texte de la dictee en cours, en trois couches du plus sur au plus fragile :
//
//   blocks   fenetres polies, definitives. C'est la SEULE couche qui part au
//            curseur : on ne frappe dans le document de l'utilisateur que du
//            texte qu'on ne reprendra plus, ce qui evite tout retour arriere.
//   phrases  phrases validees qu'aucune fenetre n'a encore absorbees. Affichees,
//            jamais frappees : une fenetre va les reecrire.
//   preview  apercu de la phrase en cours, grise. Remplace a chaque tick.
let blocks = [];
let phrases = [];
let preview = "";
let phraseCount = 0;
let injected = 0;
let injectionWarned = false;

const isContinuous = () => settings.dictation_mode === "continu";
const isPolishing = () => settings.polish_mode !== "aucun";

/// Le service continue de livrer du texte apres le relachement du raccourci :
/// la derniere phrase, puis la derniere fenetre polie, tombent pendant l'etat
/// « transcribing ». Les ignorer alors reviendrait a ne jamais les frapper dans
/// le document — c'est tout un morceau de dictee qui manquerait a la fin.
const isDelivering = () => state === "streaming" || state === "transcribing";

function resetStream() {
  blocks = [];
  phrases = [];
  preview = "";
  phraseCount = 0;
  injected = 0;
}

/// Le texte definitif en clair, l'apercu en grise a la suite. On reconstruit
/// tout a chaque evenement : une fenetre polie reecrit plusieurs phrases d'un
/// coup, un rendu incremental ne saurait pas les retirer.
function renderStream() {
  const settled = [...blocks, ...phrases].filter(Boolean).join(" ");
  el.text.textContent = settled;
  if (preview) {
    const span = document.createElement("span");
    span.className = "preview";
    span.textContent = settled ? ` ${preview}` : preview;
    el.text.appendChild(span);
  }
  el.text.scrollTop = el.text.scrollHeight;
}

function countPhrases() {
  el.hint.textContent = `${phraseCount} phrase${phraseCount > 1 ? "s" : ""}`;
}

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
  resetStream();
  invoke("hide_overlay");
}

async function beginDictation() {
  clearTimeout(hideTimer);
  manuallyDismissed = false;
  el.text.textContent = "";
  el.badge.textContent = "";
  resetStream();
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

function queueInjection(text) {
  if (!settings.inject_at_cursor || !text) return injectChain;
  const first = injected === 0;
  injected += 1;
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

// Une phrase vient d'etre transcrite. Definitive tant qu'on ne polit pas ;
// sinon elle attend la fenetre qui la reecrira avec ses voisines, et ce n'est
// donc pas encore le moment de la frapper dans le document.
bus.on("commit", ({ text }) => {
  if (!isDelivering()) return;
  clearTimeout(hideTimer);

  phrases.push(text);
  phraseCount += 1;
  preview = "";
  if (!isPolishing()) {
    queueInjection(text);
    blocks.push(...phrases.splice(0));
  }

  renderStream();
  // Apres le relachement, l'indication utile est « Dernière phrase… » : la
  // remplacer par un decompte laisserait croire que la dictee est finie.
  if (state === "streaming") countPhrases();
});

// Ce que le moteur entend de la phrase en cours, sans attendre qu'elle finisse.
// Provisoire par nature : affiche en grise, jamais frappe, jamais historise.
bus.on("preview", ({ text }) => {
  if (state !== "streaming") return;
  preview = text;
  renderStream();
});

// Une fenetre a ete re-decodee d'un bloc : elle remplace les phrases qu'elle
// recouvre. C'est ici, et seulement ici, que le texte part au curseur.
bus.on("revise", ({ text, from_index, to_index }) => {
  if (!isDelivering()) return;
  clearTimeout(hideTimer);

  phrases.splice(0, to_index - from_index + 1);
  blocks.push(text);
  queueInjection(text);

  renderStream();
});

// Silero a entendu une attaque ou une fin : de quoi montrer qu'une phrase est
// en cours de formation, plutot que de laisser croire que rien ne se passe.
bus.on("speech", ({ speaking }) => {
  if (state !== "streaming") return;
  // L'apercu survit a la fin d'une phrase : le laisser jusqu'au `commit` evite
  // que le texte disparaisse le temps du decodage. Mais une phrase rejetee — un
  // raclement de gorge dont le moteur avait tire un mot — n'amene aucun commit,
  // et son apercu resterait a l'ecran. La reprise de parole le balaie.
  if (speaking) preview = "";
  renderStream();

  if (!phraseCount) return;
  if (speaking) el.hint.textContent = "Phrase en cours…";
  else countPhrases();
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

  // En continu, tout le texte est deja parti au fil de la dictee — y compris la
  // derniere fenetre, que `finish()` polit avant d'emettre `final`. On ne rejoue
  // rien : on attend juste que la file de frappe se vide.
  if (wasStreamed) {
    await injectChain;
    resetStream();
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

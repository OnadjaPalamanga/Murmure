// Client WebSocket vers le service Python. Se reconnecte tout seul : le service
// peut redemarrer (changement de modele, mise a jour) sans qu'on relance l'UI.
//
// Le service exige un jeton de session : sans lui, n'importe quelle page web
// ouverte dans un navigateur pourrait se connecter a ce meme port et piloter la
// dictee — la politique d'origine unique ne s'applique pas aux WebSocket. Le
// jeton est un fichier de `%APPDATA%\Murmure` que seul le cote Rust peut lire.

import { getLanguage, t } from "./i18n.js";

const URL = "ws://127.0.0.1:8756/ws";
const SUBPROTOCOL = "murmure.v1";
const TOKEN_PREFIX = "murmure.token.";

// Dernier jeton obtenu. `buildAudioPlayer` en a besoin de facon synchrone pour
// composer le `src` d'une balise <audio>, ou l'on ne peut pas poser d'en-tete.
let sessionToken = "";

export const authQuery = () =>
  sessionToken ? `?token=${encodeURIComponent(sessionToken)}` : "";

async function fetchToken() {
  // Relu a CHAQUE tentative : le service en tire un neuf a chaque demarrage, et
  // l'application peut se reconnecter a un service qui vient de redemarrer.
  try {
    sessionToken = await window.__TAURI__.core.invoke("session_token");
  } catch {
    // Le service n'a pas encore ecrit son jeton : la reconnexion reessaiera.
    sessionToken = "";
  }
  return sessionToken;
}

export class Bus extends EventTarget {
  constructor() {
    super();
    this.socket = null;
    this.connected = false;
    this._retry = 0;
    this._queue = [];
    this.connect();
  }

  async connect() {
    const token = await fetchToken();
    if (!token) {
      this._scheduleReconnect();
      return;
    }

    try {
      this.socket = new WebSocket(URL, [SUBPROTOCOL, TOKEN_PREFIX + token]);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      this.connected = true;
      this._retry = 0;
      this.dispatchEvent(new CustomEvent("connection", { detail: { connected: true } }));
      for (const msg of this._queue.splice(0)) this.socket.send(msg);
    };

    this.socket.onmessage = (raw) => {
      let msg;
      try {
        msg = JSON.parse(raw.data);
      } catch {
        return;
      }
      this.dispatchEvent(new CustomEvent(msg.type, { detail: msg }));
      this.dispatchEvent(new CustomEvent("*", { detail: msg }));
    };

    this.socket.onclose = () => {
      this.connected = false;
      this.dispatchEvent(new CustomEvent("connection", { detail: { connected: false } }));
      this._scheduleReconnect();
    };

    this.socket.onerror = () => this.socket?.close();
  }

  _scheduleReconnect() {
    // Recul exponentiel plafonne a 5 s : au demarrage de Windows, le service
    // peut mettre plusieurs secondes a repondre.
    const delay = Math.min(5000, 250 * 2 ** this._retry++);
    setTimeout(() => this.connect(), delay);
  }

  send(type, payload = {}) {
    const msg = JSON.stringify({ type, ...payload });
    if (this.connected && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(msg);
    } else {
      this._queue.push(msg);
    }
  }

  on(type, handler) {
    this.addEventListener(type, (e) => handler(e.detail));
    return this;
  }
}

export const formatDuration = (seconds) => {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

// en-GB et non en-US : jour puis mois, comme en francais, et heure sur 24 h.
// Les deux langues rangent donc la date dans le meme ordre, ce qui evite de
// relire « 08/09 » deux fois pour savoir si c'est aout ou septembre.
const locale = () => (getLanguage() === "fr" ? "fr-FR" : "en-GB");

export const formatDate = (iso) => {
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  const today = new Date();
  const time = d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return t("date.today", { time });

  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return t("date.yesterday", { time });

  return `${d.toLocaleDateString(locale(), { day: "numeric", month: "short" })} ${time}`;
};

// Client WebSocket vers le service Python. Se reconnecte tout seul : le service
// peut redemarrer (changement de modele, mise a jour) sans qu'on relance l'UI.

import { getLanguage, t } from "./i18n.js";

const URL = "ws://127.0.0.1:8756/ws";

export class Bus extends EventTarget {
  constructor() {
    super();
    this.socket = null;
    this.connected = false;
    this._retry = 0;
    this._queue = [];
    this.connect();
  }

  connect() {
    try {
      this.socket = new WebSocket(URL);
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

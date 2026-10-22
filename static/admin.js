
"use strict";

const fmt = n => Number(n).toFixed(2).replace(".", ",") + " €";

function el(tag, { cls, text, attrs, on } = {}, ...children) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = String(text);
  if (attrs) for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (on) for (const [ev, fn] of Object.entries(on)) e.addEventListener(ev, fn);
  e.append(...children);
  return e;
}

const toastEl = document.getElementById("toast");
let toastTimer;
function toast(msg, err = false) {
  toastEl.textContent = msg;
  toastEl.className = "show" + (err ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastEl.className = ""), 2800);
}

async function api(url, method = "GET", body) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  if (r.status === 401 || r.status === 403) {
    location.href = "/";
    throw new Error("Neautorizovaný prístup");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = Array.isArray(data.detail) ? "Neplatný počet (1 – 100 000)" : data.detail;
    throw new Error(typeof detail === "string" ? detail : "Chyba servera");
  }
  return data;
}

async function nacitaj() {
  const sklad = await api("/api/sklad");
  document.getElementById("telo").replaceChildren(
    ...sklad.map(p => {
      const input = el("input", { attrs: { type: "number", min: "1", max: "100000", value: "10", required: "" } });
      const form = el("form", { cls: "add", on: { submit: ev => pridaj(ev, p.nazov, input) } },
        input, el("button", { text: "Pridať" }));
      return el("tr", {},
        el("td", { cls: "name", text: p.nazov }),
        el("td", { text: p.kategoria }),
        el("td", { text: fmt(p.cena) }),
        el("td", { cls: p.kusy < 5 ? "low" : "", text: Number(p.kusy) + " ks" }),
        el("td", {}, form)
      );
    })
  );
}

async function pridaj(ev, nazov, input) {
  ev.preventDefault();
  const pocet = parseInt(input.value, 10);
  if (!Number.isInteger(pocet) || pocet < 1 || pocet > 100000) {
    toast("Neplatný počet (1 – 100 000)", true);
    return;
  }
  try {
    const d = await api(`/api/sklad/${encodeURIComponent(nazov)}/pridaj`, "POST", { pocet });
    toast(d.sprava);
  } catch (e) {
    toast(e.message, true);
  }
  nacitaj();
}

document.getElementById("btnReset").addEventListener("click", async () => {
  if (!confirm("Naozaj resetovať sklad a košík?")) return;
  try {
    const d = await api("/api/reset", "POST");
    toast(d.sprava);
  } catch (e) {
    toast(e.message, true);
  }
  nacitaj();
});

document.getElementById("btnLogout").addEventListener("click", async () => {
  try { await api("/api/logout", "POST"); } catch (_) {}
  location.href = "/";
});

nacitaj();

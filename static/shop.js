"use strict";
 
const EMOJI = { ovocie: "🍎", zelenina: "🥕", sladkosti: "🍫" };
const fmt = n => Number(n).toFixed(2).replace(".", ",") + " €";
 
// Bezpečné vytváranie prvkov: všetko ide cez textContent, nikdy cez innerHTML
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
  toastTimer = setTimeout(() => (toastEl.className = ""), 2500);
}
 
async function api(url, method = "GET", body) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Chyba servera");
  return data;
}
 
async function nacitajSklad() {
  const sklad = await api("/api/sklad");
  document.getElementById("produkty").replaceChildren(
    ...sklad.map(p => {
      const btn = el("button", {
        text: "Do košíka",
        on: { click: () => akcia("/api/kosik/" + encodeURIComponent(p.nazov)) },
      });
      btn.disabled = !(p.kusy > 0);
      return el("div", { cls: "card" },
        el("div", { cls: "emoji", text: EMOJI[p.kategoria] || "📦" }),
        el("div", { cls: "name", text: p.nazov }),
        el("div", { cls: "meta", text: p.kategoria }),
        el("div", { cls: "price", text: fmt(p.cena) }),
        el("div", {
          cls: "meta" + (p.kusy < 5 ? " stock-low" : ""),
          text: p.kusy > 0 ? "Na sklade: " + Number(p.kusy) + " ks" : "Vypredané",
        }),
        btn
      );
    })
  );
}
 
function vykresliKosik(k) {
  const rows = k.polozky.map(p =>
    el("div", { cls: "row" },
      el("span", {}, el("span", { cls: "name", text: p.nazov }), document.createTextNode(" × " + Number(p.pocet))),
      el("span", { text: fmt(p.cena * p.pocet) })
    )
  );
  if (k.zlava) {
    rows.push(el("div", { cls: "row" }, el("span", { text: "Zľava" }), el("span", { cls: "badge", text: "−10 %" })));
  }
  if (!rows.length) rows.push(el("div", { cls: "empty", text: "Košík je prázdny." }));
  document.getElementById("kosik").replaceChildren(...rows);
  document.getElementById("spolu").textContent = fmt(k.celkova_cena);
  document.getElementById("btnZlava").disabled = !!k.zlava || !k.polozky.length;
}
 
async function nacitajKosik() { vykresliKosik(await api("/api/kosik")); }
 
async function akcia(url, method = "POST") {
  try {
    const d = await api(url, method);
    toast(d.sprava || "Hotovo");
    if (d.polozky) vykresliKosik(d);
  } catch (e) {
    toast(e.message, true);
  }
  await nacitajSklad();
}
 
document.getElementById("btnZlava").addEventListener("click", () => akcia("/api/zlava"));
document.getElementById("btnReset").addEventListener("click", () => akcia("/api/vymaz-kosik"));
 
// ---- Prihlásenie do adminu (overuje SERVER, nie prehliadač) ----
const modal = document.getElementById("modal");
const heslo = document.getElementById("heslo");
 
function otvorModal() {
  heslo.value = "";
  heslo.classList.remove("bad");
  modal.classList.add("show");
  heslo.focus();
}
function zavriModal() { modal.classList.remove("show"); }
 
document.getElementById("loginForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  try {
    await api("/api/login", "POST", { heslo: heslo.value });
    location.href = "/admin";
  } catch (e) {
    heslo.classList.add("bad");
    heslo.select();
    toast(e.message || "Nesprávne heslo", true);
  }
});
document.getElementById("btnZrusit").addEventListener("click", zavriModal);
 
document.addEventListener("keydown", e => {
  if (e.key === "F1") {
    e.preventDefault();
    otvorModal();
  } else if (e.key === "Escape") {
    zavriModal();
  }
});
 
nacitajSklad();
nacitajKosik();
 

import "./console.css";
import { clampFollowupSec } from "./followup";
import {
  brainPage,
  chatPage,
  homePage,
  linksPage,
  lookPage,
  memoryPage,
  personaPage,
  skillsPage,
  systemPage,
  voicePage,
  type Refresher,
} from "./console/pages";
import { pausePreview } from "./console/preview";
import { fetchJson, icon, live } from "./console/ui";
import { openWizard } from "./console/wizard";
import { openSetup } from "./console/setup";
import { setupDone } from "./console/setup-logic";

type Page = { id: string; title: string; icon: string; render: (root: HTMLElement, nav: (p: string) => void) => Promise<Refresher> };

const PAGES: Page[] = [
  { id: "home", title: "Главная", icon: "i-home", render: homePage },
  { id: "persona", title: "Персона", icon: "i-user", render: personaPage },
  { id: "look", title: "Внешность", icon: "i-shirt", render: lookPage },
  { id: "voice", title: "Голос", icon: "i-wave", render: voicePage },
  { id: "chat", title: "Чат", icon: "i-chat", render: chatPage },
  { id: "memory", title: "Память", icon: "i-book", render: memoryPage },
  { id: "skills", title: "Навыки", icon: "i-bolt", render: skillsPage },
  { id: "links", title: "Связи", icon: "i-link", render: linksPage },
  { id: "brain", title: "Мозг", icon: "i-cpu", render: brainPage },
  { id: "system", title: "Система", icon: "i-sliders", render: systemPage },
];

const ICONS = `
<svg class="sprite" aria-hidden="true">
  <symbol id="i-home" viewBox="0 0 24 24"><path d="M3 11.5 12 4l9 7.5M5.5 9.5V20h13V9.5"/></symbol>
  <symbol id="i-user" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></symbol>
  <symbol id="i-shirt" viewBox="0 0 24 24"><path d="M8 3 3 6l2 4 2-1v12h10V9l2 1 2-4-5-3a4 4 0 0 1-8 0z"/></symbol>
  <symbol id="i-wave" viewBox="0 0 24 24"><path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10M21 12h0"/></symbol>
  <symbol id="i-chat" viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-4.9A8 8 0 1 1 21 12z"/></symbol>
  <symbol id="i-book" viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 19V5M8 7h7"/></symbol>
  <symbol id="i-bolt" viewBox="0 0 24 24"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></symbol>
  <symbol id="i-link" viewBox="0 0 24 24"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></symbol>
  <symbol id="i-cpu" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></symbol>
  <symbol id="i-sliders" viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></symbol>
  <symbol id="i-spark" viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></symbol>
  <symbol id="i-moon" viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></symbol>
  <symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></symbol>
  <symbol id="i-heart" viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></symbol>
  <symbol id="i-play" viewBox="0 0 24 24"><path d="M8 5.5v13l10-6.5z" fill="currentColor" stroke="none"/></symbol>
  <symbol id="i-arrow" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></symbol>
  <symbol id="i-send" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></symbol>
  <symbol id="i-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></symbol>
  <symbol id="i-stop" viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="2.5"/></symbol>
  <symbol id="i-up" viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></symbol>
  <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/></symbol>
</svg>`;

const root = document.getElementById("console-root")!;
root.innerHTML = `${ICONS}
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><div class="logo">${icon("i-spark")}</div><div><b id="brand-name">Aya</b><small>Studio</small></div></div>
      <nav class="nav">${PAGES.map((p) => `<button class="nav-btn" data-page="${p.id}" title="${p.title}">${icon(p.icon)}<span>${p.title}</span></button>`).join("")}</nav>
      <button class="nav-btn accent" id="nav-wizard" title="Собрать свою Аю">${icon("i-spark")}<span>Собрать Аю</span></button>
      <div class="side-foot">
        <span class="core-dot" id="core-dot" title="ядро"></span>
        <button class="icon-btn" id="refresh-btn" title="Обновить страницу">${icon("i-refresh")}</button>
        <button class="icon-btn" id="theme-btn" title="Светлая/тёмная тема">${icon("i-moon")}</button>
      </div>
    </aside>
    <main class="main" id="tab-body"></main>
  </div>`;

const body = document.getElementById("tab-body")!;
const dot = document.getElementById("core-dot")!;
let current = "home";
let refresher: Refresher = undefined;
let renderSeq = 0;

function pageFromHash(): string {
  const h = location.hash.replace("#", "");
  return PAGES.some((p) => p.id === h) ? h : "home";
}

async function render(page: string) {
  const seq = ++renderSeq;
  current = page;
  live.followupInput = live.followupSec = live.focusInput = null;
  document.querySelectorAll<HTMLButtonElement>(".nav-btn[data-page]").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  pausePreview();
  const holder = document.createElement("div");
  holder.className = "page";
  const def = PAGES.find((p) => p.id === page) ?? PAGES[0];
  try {
    const r = await def.render(holder, nav);
    if (seq !== renderSeq) return; // успели уйти на другую страницу
    refresher = r;
  } catch (e) {
    holder.innerHTML = `<div class="card"><h3 class="sec-title">Не получилось открыть</h3><div class="hint-box">${String(e)}</div></div>`;
  }
  if (seq !== renderSeq) return;
  body.replaceChildren(holder);
  body.scrollTop = 0;
}

function nav(page: string) {
  if (page === "setup") {
    if (location.hash === "#setup") history.replaceState(null, "", `#${current}`);
    void openSetup();
    return;
  }
  if (page === "wizard") {
    void openWizard(() => void render(current));
    return;
  }
  if (location.hash !== `#${page}`) location.hash = page;
  else void render(page);
}

window.addEventListener("hashchange", () => {
  const h = location.hash.replace("#", "");
  if (h === "setup") {
    nav("setup");
    return;
  }
  if (h === "wizard") {
    history.replaceState(null, "", `#${current}`);
    nav("wizard");
    return;
  }
  void render(pageFromHash());
});

document.querySelectorAll<HTMLButtonElement>(".nav-btn[data-page]").forEach((b) => b.addEventListener("click", () => nav(b.dataset.page!)));
document.getElementById("nav-wizard")!.addEventListener("click", () => nav("wizard"));
document.getElementById("refresh-btn")!.addEventListener("click", () => void render(current));

/* тема */
const themeBtn = document.getElementById("theme-btn")!;
function applyTheme(t: string) {
  document.documentElement.dataset.theme = t;
  themeBtn.innerHTML = icon(t === "light" ? "i-sun" : "i-moon");
  try {
    localStorage.setItem("aya.theme", t);
  } catch {}
}
applyTheme((() => {
  try {
    return localStorage.getItem("aya.theme") || "dark";
  } catch {
    return "dark";
  }
})());
themeBtn.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"));

/* главное окно просит открыть раздел (например, мастер) */
void (async () => {
  try {
    const { listen } = await import("@tauri-apps/api/event");
    await listen<string>("aya-nav", (e) => nav(String(e.payload || "home")));
  } catch {
    /* консоль открыта в браузере */
  }
})();

/* живые переключатели (свободные руки, тишина) — их меняют из трея и окна Аи */
async function syncLiveControls() {
  if (document.hidden) return;
  if (live.followupInput?.isConnected || live.followupSec?.isConnected) {
    try {
      const cfg = await fetchJson<{ followup?: boolean; followup_sec?: number }>("/api/config");
      if (live.followupInput?.isConnected && document.activeElement !== live.followupInput) live.followupInput.checked = !!cfg.followup;
      if (live.followupSec?.isConnected && document.activeElement !== live.followupSec) live.followupSec.value = String(clampFollowupSec(cfg.followup_sec ?? 6));
    } catch {}
  }
  if (live.focusInput?.isConnected) {
    try {
      const p = await fetchJson<{ focus?: boolean }>("/api/proactive");
      if (document.activeElement !== live.focusInput) live.focusInput.checked = !!p.focus;
    } catch {}
  }
}

async function heartbeat() {
  if (document.hidden) return; // окно консоли свёрнуто — не опрашиваем ядро
  try {
    const cfg = await fetchJson<{ persona_name?: string }>("/api/config");
    dot.classList.add("ok");
    document.getElementById("brand-name")!.textContent = cfg.persona_name || "Aya";
  } catch {
    dot.classList.remove("ok");
  }
  if (typeof refresher === "function" && (current === "home" || current === "system")) {
    try {
      await refresher();
    } catch {}
  }
}

window.setInterval(() => void syncLiveControls(), 4000);
window.setInterval(() => void heartbeat(), 15000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) pausePreview();
  else void heartbeat();
});

const first = location.hash.replace("#", "");
void render(pageFromHash()).then(async () => {
  void heartbeat();
  if (first === "wizard") return nav("wizard");
  if (first === "setup") return nav("setup");
  // первый запуск: сначала мастер настройки, без него Ая не покажется
  if ((await setupDone(() => fetchJson<{ done: boolean }>("/api/setup"))) === false) return nav("setup");
  // персона ещё не собрана — предлагаем «Собрать Аю» один раз
  try {
    const v = await fetchJson<{ profile: { configured: boolean } }>("/api/persona");
    if (!v.profile.configured && localStorage.getItem("aya.wizardOffered") !== "1") {
      localStorage.setItem("aya.wizardOffered", "1");
      nav("wizard");
    }
  } catch {}
});

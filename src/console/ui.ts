/* Общие помощники консоли. */

import { CORE_URL } from "../persona";
import { runGuarded } from "./guard";

export const CORE = CORE_URL;

/** Живые контролы, которые синхронизируются с ядром, пока открыта страница. */
export const live: {
  followupInput: HTMLInputElement | null;
  followupSec: HTMLInputElement | null;
  focusInput: HTMLInputElement | null;
} = { followupInput: null, followupSec: null, focusInput: null };

export async function fetchJson<T = unknown>(path: string): Promise<T> {
  const r = await fetch(CORE + path);
  if (!r.ok) throw new Error(String(r.status));
  return (await r.json()) as T;
}

export async function postJson(path: string, body: unknown): Promise<Record<string, unknown>> {
  const r = await fetch(`${CORE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  try {
    return (await r.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export function esc(s: unknown): string {
  return String(s ?? "").replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string,
  );
}

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

export function switchEl(checked: boolean, onChange: (v: boolean) => Promise<void>): HTMLElement {
  const sw = el("label", "switch");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", async () => {
    const want = input.checked;
    await runGuarded(
      () => onChange(want),
      (msg) => {
        input.checked = !want; // не сохранилось — переключатель возвращаем
        sw.title = msg;
      },
    );
  });
  sw.append(input, el("span", "slider"));
  return sw;
}

export function actionBtn(text: string, onClick: (b: HTMLButtonElement) => Promise<void>, cls = "top-btn"): HTMLButtonElement {
  const b = el("button", cls, text) as HTMLButtonElement;
  b.addEventListener("click", async () => {
    b.disabled = true;
    const label = b.innerHTML;
    try {
      await runGuarded(
        () => onClick(b),
        (msg) => {
          b.textContent = msg; // сбой видно прямо на кнопке пару секунд
          window.setTimeout(() => (b.innerHTML = label), 2500);
        },
      );
    } finally {
      b.disabled = false;
    }
  });
  return b;
}

export function metricRow(label: string, value: unknown): string {
  return `<div class="metric"><span>${esc(label)}</span><span>${esc(value ?? "—")}</span></div>`;
}

/** Заголовок страницы: надзаголовок, заголовок, подзаголовок. */
export function pageHead(kicker: string, title: string, sub = ""): HTMLElement {
  const h = el("header", "page-head");
  h.append(el("div", "kicker", kicker), el("h1", "page-title", title));
  if (sub) h.append(el("p", "page-sub", sub));
  return h;
}

export function icon(id: string, cls = "icon"): string {
  return `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
}

let toastTimer = 0;
export function toast(text: string) {
  let t = document.getElementById("c-toast");
  if (!t) {
    t = el("div", "c-toast");
    t.id = "c-toast";
    document.body.append(t);
  }
  t.textContent = text;
  t.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => t!.classList.remove("show"), 2200);
}

/** Сетка карточек (две колонки на широком окне). */
export function grid(...cards: HTMLElement[]): HTMLElement {
  const g = el("div", "card-grid");
  g.append(...cards);
  return g;
}

/* Память: дневник Аи и поиск по прошлым разговорам. */
import { el, esc, fetchJson, postJson, switchEl, toast } from "./ui";

type Entry = { day: string; ts: number; text: string; mood: string; messages: number };

const MOOD_DOT: Record<string, string> = { joyful: "☀", sad: "☂", thoughtful: "✎", surprise: "✦", calm: "☾" };

function dayTitle(day: string): string {
  const d = new Date(`${day}T12:00:00`);
  const today = new Date();
  const y = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return "Сегодня";
  if (d.toDateString() === y.toDateString()) return "Вчера";
  return d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
}

export function diaryCard(): HTMLElement {
  const card = el("div", "card diary");
  const head = el("div", "diary-head");
  head.append(el("h3", "sec-title", "Дневник"));
  const sw = switchEl(true, async (v) => {
    await postJson("/api/config", { diary_enabled: v });
    toast(v ? "Буду вести дневник каждый вечер" : "Дневник на паузе");
  });
  sw.title = "писать запись каждый вечер";
  const write = el("button", "top-btn sm", "Написать за сегодня") as HTMLButtonElement;
  head.append(sw, write);
  const list = el("div", "diary-list");
  card.append(head, el("div", "hint-box", "Каждый вечер после 22:00 она сама пишет пару строк о вашем дне — по реальным разговорам. Если ПК был выключен, допишет утром."), list);

  const load = async () => {
    const r = await fetchJson<{ entries: Entry[]; enabled: boolean }>("/api/diary").catch(() => ({ entries: [] as Entry[], enabled: true }));
    (sw.querySelector("input") as HTMLInputElement).checked = r.enabled;
    list.innerHTML = r.entries.length
      ? r.entries
          .map(
            (e) => `<article class="diary-entry">
              <header><b>${esc(dayTitle(e.day))}</b><span class="dim">${esc(MOOD_DOT[e.mood] || "☾")} · ${e.messages} сообщ.</span>
              <button class="top-btn sm" data-del="${esc(e.day)}" title="Удалить запись">×</button></header>
              <p>${esc(e.text)}</p></article>`,
          )
          .join("")
      : `<div class="hint-box">Записей пока нет — первая появится вечером.</div>`;
    list.querySelectorAll<HTMLButtonElement>("[data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Удалить эту запись дневника?")) return;
        await postJson("/api/diary/delete", { day: b.dataset.del });
        await load();
      }),
    );
  };
  write.addEventListener("click", async () => {
    write.disabled = true;
    write.textContent = "пишет…";
    const r = await postJson("/api/diary/write", {});
    write.disabled = false;
    write.textContent = "Написать за сегодня";
    if (!r.ok) toast(String(r.error || "не получилось"));
    await load();
  });
  void load();
  return card;
}

export function memorySearchCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Что она помнит из разговоров"));
  const inp = el("input", "inp") as HTMLInputElement;
  inp.placeholder = "поиск по смыслу: отпуск, мой кот, работа…";
  const out = el("div", "mem-results");
  let t = 0;
  inp.addEventListener("input", () => {
    window.clearTimeout(t);
    t = window.setTimeout(async () => {
      const q = inp.value.trim();
      if (q.length < 2) {
        out.innerHTML = "";
        return;
      }
      out.innerHTML = `<div class="hint-box">ищу…</div>`;
      type R = { score: number; ts: number; role: string; text: string };
      const r = await fetchJson<{ results: R[] }>(`/api/memory/search?q=${encodeURIComponent(q)}`).catch(() => ({ results: [] as R[] }));
      out.innerHTML = r.results.length
        ? r.results
            .map(
              (x) => `<div class="mem-hit ${x.role}"><span class="dim">${new Date(x.ts * 1000).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" })} · ${
                x.role === "user" ? "ты" : "она"
              }</span><p>${esc(x.text)}</p></div>`,
            )
            .join("")
        : `<div class="hint-box">ничего похожего не нашлось</div>`;
    }, 350);
  });
  card.append(inp, out);
  return card;
}

/* Страницы консоли. Каждая функция наполняет `root` и возвращает необязательный
 * «освежитель» — его оболочка зовёт по таймеру, пока страница открыта. */
import { hotkeysCard } from "./hotkey-field";
import {
  TOOLS,
  TTS_VOICES,
  agentsCard,
  discordCard,
  gpuCard,
  llmCard,
  mcpCard,
  memoryCard,
  permsCard,
  proactiveCard,
  shellCard,
  tasksCard,
  tgCard,
  tgUserCard,
  voiceCard,
  workspaceCard,
} from "./cards";
import { MODULES, TRAITS, summary, type PersonaView } from "./persona-store";
import { showPreview, wavePreview } from "./preview";
import { CORE, actionBtn, el, esc, fetchJson, grid, icon, metricRow, pageHead, postJson, switchEl, toast } from "./ui";
import { notifyMainReload, openWizard } from "./wizard";
import { customVoicesCard, fishCard, handsFreeCard, musicCard, sttCard } from "./voice-extra";
import { diaryCard, memorySearchCard } from "./memory-extra";

export type Refresher = (() => Promise<void> | void) | void;
export type Nav = (page: string) => void;

const RU_MOOD: Record<string, string> = {
  calm: "спокойная",
  joyful: "радостная",
  thoughtful: "задумчивая",
  sad: "грустит",
  surprise: "удивлена",
};
const RU_ACT: Record<string, string> = {
  rest: "отдыхает",
  talk: "разговаривает",
  read: "читает",
  sketch: "рисует",
  task: "занята делом",
  idle: "ждёт",
};

function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

/* ═════════════ Главная ═════════════ */

export async function homePage(root: HTMLElement, nav: Nav): Promise<Refresher> {
  type Cfg = { tts_voice_id?: string; tts_volume?: number; voice_mode?: string; live2d_model_file?: string; tts_enabled?: boolean };
  const [persona, cfg, state, hist, facts, perms, mcp, metrics] = await Promise.all([
    fetchJson<PersonaView>("/api/persona").catch(() => null),
    fetchJson<Cfg>("/api/config").catch(() => ({}) as Cfg),
    fetchJson<Record<string, unknown>>("/api/state").catch(() => ({}) as Record<string, unknown>),
    fetchJson<{ role: string; text: string }[]>("/api/history?limit=12").catch(() => []),
    fetchJson<{ facts: { key: string; value: string }[] }>("/api/memory").catch(() => ({ facts: [] })),
    fetchJson<Record<string, boolean>>("/api/permissions").catch(() => ({}) as Record<string, boolean>),
    fetchJson<Record<string, { connected: boolean }>>("/api/mcp").catch(() => ({}) as Record<string, { connected: boolean }>),
    fetchJson<{ svc?: Record<string, unknown> }>("/api/debug/metrics").catch(() => ({}) as { svc?: Record<string, unknown> }),
  ]);
  const p = persona?.profile;
  const name = p?.name || "Ая";
  const lastAya = [...hist].reverse().find((m) => m.role !== "user")?.text || p?.greeting || "Привет! Я рядом.";
  const voice = TTS_VOICES.find((v) => v.id === cfg.tts_voice_id);
  const enabledTools = Object.values(perms).filter(Boolean).length;
  const mcpOn = Object.values(mcp).filter((s) => s.connected).length;
  const roleTitle = persona?.roles.find((r) => r.id === p?.role)?.title || "Компаньонка";
  const svc = metrics.svc || {};

  root.innerHTML = `
    <header class="top">
      <div><div class="kicker">${new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}</div><h2 class="greet">${greeting()}</h2></div>
      <button class="btn grad" data-go="chat">${icon("i-chat")}Поговорить</button>
    </header>
    <section class="hero">
      <div class="hero-txt">
        <div class="live-chip" id="live-chip"><i></i>${esc(RU_MOOD[String(state.mood)] || "рядом")} · ${esc(RU_ACT[String(state.activity)] || "ждёт")}</div>
        <h1 class="hero-name">${esc(name)}<span class="spark">${icon("i-spark")}</span></h1>
        <p class="hero-sub">${esc(roleTitle)}${p?.user_name ? ` · для ${esc(p.user_name)}` : ""}</p>
        <blockquote class="quote">«${esc(lastAya.slice(0, 180))}${lastAya.length > 180 ? "…" : ""}»</blockquote>
        <div class="chips">${(p ? [summary(p).split(", ")[0], ...p.tags.slice(0, 3)] : []).filter(Boolean).map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>
        <div class="hero-actions">
          <button class="btn grad" data-go="chat">${icon("i-chat")}Поговорить</button>
          <button class="btn ghost" data-wizard>${icon("i-spark")}${p?.configured ? "Пересобрать" : "Собрать свою Аю"}</button>
        </div>
      </div>
      <div class="hero-stage">
        <div class="arch" id="home-stage"></div>
        <span class="float-tag top-right"><i class="dot-ok"></i>Live2D · ${esc((cfg.live2d_model_file || "haru").split("/")[0])}</span>
        <span class="float-tag bottom-left">${cfg.tts_enabled === false ? "без голоса" : `голос · ${esc(voice?.label || cfg.tts_voice_id || "—")}`}</span>
      </div>
    </section>
    ${!p?.configured ? `<div class="banner">${icon("i-spark")}<div><b>Сделай Аю своей.</b> Пять шагов: имя, внешность, голос, характер и возможности — меньше минуты.</div><button class="btn grad" data-wizard>Начать</button></div>` : ""}
    <div class="card-grid four">
      <article class="card tile" data-go="persona">
        <div class="tile-head">${icon("i-user")}<div><h3>Характер</h3><small>${esc(p ? summary(p) : "по умолчанию")}</small></div></div>
        ${TRAITS.map((t) => `<div class="bar"><span>${t.title}</span><i style="--v:${p?.traits[t.id] ?? 50}%"></i><b>${p?.traits[t.id] ?? 50}</b></div>`).join("")}
      </article>
      <article class="card tile" data-go="voice">
        <div class="tile-head">${icon("i-wave")}<div><h3>Голос</h3><small>${esc(voice?.sub || "озвучка")}</small></div></div>
        <div class="voice-hero"><span class="opt-ava">${esc((voice?.label || "А").slice(0, 1))}</span><div><b>${esc(voice?.label || cfg.tts_voice_id || "—")}</b><small>громкость ${cfg.tts_volume ?? 100}% · ${esc({ button: "по кнопке", auto: "по голосу", hotkey: "клавишей" }[cfg.voice_mode || "button"] || "")}</small></div></div>
        <div class="wavebar">${Array.from({ length: 28 }, (_, i) => `<i style="--h:${20 + Math.round(Math.abs(Math.sin(i * 1.7)) * 70)}%"></i>`).join("")}</div>
      </article>
      <article class="card tile" data-go="memory">
        <div class="tile-head">${icon("i-book")}<div><h3>Память</h3><small>${facts.facts.length} фактов о тебе</small></div></div>
        <ul class="mini-list">${facts.facts.slice(0, 4).map((f) => `<li><b>${esc(f.key)}</b> ${esc(f.value)}</li>`).join("") || "<li class='dim'>пока ничего — расскажи о себе</li>"}</ul>
      </article>
      <article class="card tile" data-go="skills">
        <div class="tile-head">${icon("i-bolt")}<div><h3>Навыки</h3><small>${enabledTools} из ${Object.keys(TOOLS).length} включено${mcpOn ? ` · MCP: ${mcpOn}` : ""}</small></div></div>
        <div class="mod-pills">${MODULES.map((m) => `<span class="pill-s${p?.modules[m.id] ? " on" : ""}">${m.title}</span>`).join("")}</div>
      </article>
    </div>
    <div class="card-grid two">
      <article class="card">
        <h3 class="sec-title">Сейчас</h3>
        <div id="now-box">${metricRow("настроение", RU_MOOD[String(state.mood)] || state.mood)}${metricRow("занятие", RU_ACT[String(state.activity)] || state.activity)}${metricRow("энергия", state.energy)}</div>
      </article>
      <article class="card">
        <h3 class="sec-title">Скорость</h3>
        ${metricRow("первый токен LLM, мс (средн.)", svc.llm_first_ms_avg ?? "—")}
        ${metricRow("озвучка из кэша, %", svc.tts_cache_pct ?? "—")}
        ${metricRow("сбоев LLM", svc.llm_errors ?? 0)}
      </article>
    </div>`;
  root.querySelectorAll<HTMLElement>("[data-go]").forEach((b) => b.addEventListener("click", () => nav(b.dataset.go!)));
  root.querySelectorAll<HTMLElement>("[data-wizard]").forEach((b) => b.addEventListener("click", () => void openWizard(() => nav("home"))));
  const stage = root.querySelector<HTMLElement>("#home-stage")!;
  void showPreview(stage, cfg.live2d_model_file || "haru/Haru.model3.json");
  stage.addEventListener("click", wavePreview);

  return async () => {
    try {
      const s = await fetchJson<Record<string, unknown>>("/api/state");
      const chip = root.querySelector("#live-chip");
      if (chip) chip.innerHTML = `<i></i>${esc(RU_MOOD[String(s.mood)] || "рядом")} · ${esc(RU_ACT[String(s.activity)] || "ждёт")}`;
      const box = root.querySelector("#now-box");
      if (box) box.innerHTML = metricRow("настроение", RU_MOOD[String(s.mood)] || s.mood) + metricRow("занятие", RU_ACT[String(s.activity)] || s.activity) + metricRow("энергия", s.energy);
    } catch {}
  };
}

/* ═════════════ Персона ═════════════ */

export async function personaPage(root: HTMLElement, nav: Nav): Promise<Refresher> {
  const v = await fetchJson<PersonaView>("/api/persona").catch(() => null);
  root.append(pageHead("персона", "Характер и личности", "Собери Аю в конструкторе или создай отдельные личности-агентов со своим SOUL.md."));
  const card = el("div", "card persona-card");
  if (v) {
    const p = v.profile;
    card.innerHTML = `
      <div class="persona-top">
        <div class="who-mark big">${esc(p.name.slice(0, 1))}</div>
        <div><h3>${esc(p.name)}</h3><small>${esc(v.roles.find((r) => r.id === p.role)?.title || "")} · на «${p.address}» · ${{ short: "коротко", medium: "средне", long: "развёрнуто" }[p.length]}</small></div>
        <button class="btn grad" data-wizard>${icon("i-spark")}Открыть конструктор</button>
      </div>
      ${TRAITS.map((t) => `<div class="bar"><span>${t.title}</span><i style="--v:${p.traits[t.id]}%"></i><b>${p.traits[t.id]}</b></div>`).join("")}
      <div class="chips">${p.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>
      <label class="field"><span>Дополнительные указания <small>— свободный текст, она будет его соблюдать</small></span>
        <textarea class="inp" id="p-about" rows="4" maxlength="1200" placeholder="Например: не напоминай про сон после полуночи; зови меня «капитан»">${esc(p.about)}</textarea></label>
      <div class="row-actions" id="p-actions"></div>`;
    const actions = card.querySelector<HTMLElement>("#p-actions")!;
    const save = el("button", "top-btn", "Сохранить указания");
    save.addEventListener("click", async () => {
      const r = await postJson("/api/persona", { profile: { about: (card.querySelector("#p-about") as HTMLTextAreaElement).value } });
      toast(r.ok ? "Сохранено" : "Не удалось сохранить");
    });
    const exp = el("button", "top-btn", "Экспорт в файл");
    exp.addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(p, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `aya-persona-${p.name}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
    const imp = el("button", "top-btn", "Импорт из файла");
    imp.addEventListener("click", () => {
      const f = document.createElement("input");
      f.type = "file";
      f.accept = "application/json,.json";
      f.onchange = async () => {
        const file = f.files?.[0];
        if (!file) return;
        try {
          const data = JSON.parse(await file.text());
          const r = await postJson("/api/persona", { profile: data });
          toast(r.ok ? `Персона «${String((r.profile as { name?: string })?.name || "")}» загружена` : "Файл не подошёл");
          nav("persona");
        } catch {
          toast("Это не файл персоны");
        }
      };
      f.click();
    });
    const reset = el("button", "top-btn danger", "Сбросить к стандартной");
    reset.addEventListener("click", async () => {
      if (!confirm("Вернуть стандартную Аю? Твой профиль персоны будет удалён (модули и разрешения не меняются).")) return;
      await postJson("/api/persona/reset", {});
      toast("Стандартная Ая");
      nav("persona");
    });
    actions.append(save, exp, imp, reset);
    card.querySelector("[data-wizard]")!.addEventListener("click", () => void openWizard(() => nav("persona")));
  } else card.textContent = "ядро не отвечает";
  root.append(card, agentsCard());
}

/* ═════════════ Внешность ═════════════ */

export async function lookPage(root: HTMLElement, nav: Nav): Promise<Refresher> {
  type ModelInfo = { active: boolean; file: string };
  const [models, cfg] = await Promise.all([
    fetchJson<Record<string, ModelInfo>>("/api/models").catch(() => ({}) as Record<string, ModelInfo>),
    fetchJson<{ avatar_reactions?: boolean; avatar_idle_life?: boolean; avatar_walk?: boolean; avatar_scale?: number; live2d_model_file?: string }>("/api/config").catch(() => ({}) as Record<string, never>),
  ]);
  root.append(pageHead("внешность", "Как она выглядит", "Live2D-модели из ~/.aya/models. Клик — посмотреть, «Выбрать» — поставить на экран."));
  const wrap = el("div", "look-grid big");
  const list = el("div", "look-list");
  const stage = el("div", "look-stage tall");
  wrap.append(list, stage);
  const entries = Object.entries(models);
  let shown = entries.find(([, m]) => m.active)?.[1].file || cfg.live2d_model_file || "haru/Haru.model3.json";
  const draw = () => {
    list.innerHTML = entries.length
      ? entries
          .map(
            ([n, m]) => `<div class="opt${m.file === shown ? " active" : ""}" data-file="${esc(m.file)}">
              <span class="opt-label">${esc(n)}</span><span class="opt-sub">${m.active ? "на экране" : "Live2D"}</span>
              ${m.active ? `<span class="opt-check">✓</span>` : `<button class="top-btn sm" data-pick="${esc(n)}">Выбрать</button>`}</div>`,
          )
          .join("")
      : `<div class="hint-box"><b>Положи модель Live2D</b> (папку с model3.json) в <code class="mono">%USERPROFILE%\\.aya\\models\\&lt;имя&gt;</code>.</div>`;
    list.querySelectorAll<HTMLElement>("[data-file]").forEach((o) =>
      o.addEventListener("click", (e) => {
        if ((e.target as HTMLElement).closest("[data-pick]")) return;
        shown = o.dataset.file!;
        draw();
        void showPreview(stage, shown);
      }),
    );
    list.querySelectorAll<HTMLButtonElement>("[data-pick]").forEach((b) =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        const r = await postJson("/api/models", { name: b.dataset.pick });
        if (r.ok) {
          await notifyMainReload();
          toast("Модель сменена — Ая переоденется через секунду");
          nav("look");
        } else toast(String(r.error || "не удалось"));
      }),
    );
  };
  draw();
  void showPreview(stage, shown);
  stage.addEventListener("click", wavePreview);

  const beh = el("div", "card");
  beh.append(el("h3", "sec-title", "Поведение аватара"));
  const row = (label: string, sub: string, key: string, val: boolean) => {
    const li = el("div", "set-item");
    const t = el("span", "grow");
    t.innerHTML = `${esc(label)}<small class="dim block">${esc(sub)}</small>`;
    li.append(
      t,
      switchEl(val, async (v) => {
        await postJson("/api/config", { [key]: v });
      }),
    );
    return li;
  };
  beh.append(
    row("Реакции на касания", "клик по голове — гладишь, по телу — тыкаешь; двойной клик — чат", "avatar_reactions", cfg.avatar_reactions !== false),
    row("Жизнь в простое", "оглядывается, меняет позы, дремлет, когда тебя долго нет", "avatar_idle_life", cfg.avatar_idle_life !== false),
    row("Прогулки по экрану", "раз в несколько минут сама переходит вдоль панели задач или садится на край активного окна", "avatar_walk", !!cfg.avatar_walk),
  );
  const scale = el("div", "set-item");
  const sIn = document.createElement("input");
  sIn.type = "range";
  sIn.min = "50";
  sIn.max = "160";
  sIn.step = "5";
  sIn.value = String(Math.round((cfg.avatar_scale ?? 1) * 100));
  const sOut = el("span", "mono", `${sIn.value}%`);
  let st = 0;
  sIn.addEventListener("input", () => {
    sOut.textContent = `${sIn.value}%`;
    window.clearTimeout(st);
    st = window.setTimeout(() => void postJson("/api/config", { avatar_scale: Number(sIn.value) / 100 }), 300);
  });
  scale.append(el("span", "", "Размер на экране"), sIn, sOut);
  beh.append(scale, el("div", "hint-box", "Размер также меняется Ctrl+колесо над Аей и в меню по правому клику."));
  root.append(wrap, beh);
}

/* ═════════════ Голос ═════════════ */

export async function voicePage(root: HTMLElement): Promise<Refresher> {
  root.append(pageHead("голос", "Голос и микрофон", "Каким голосом она говорит, как слушает тебя и когда заговаривает сама."));
  const cfg = await fetchJson<{ tts_enabled?: boolean }>("/api/config").catch(() => ({}) as { tts_enabled?: boolean });
  const on = el("div", "card");
  const li = el("div", "set-item");
  const t = el("span", "grow");
  t.innerHTML = `Озвучивать ответы<small class="dim block">выключи — останется текст и пузырь над головой</small>`;
  li.append(
    t,
    switchEl(cfg.tts_enabled !== false, async (v) => {
      await postJson("/api/config", { tts_enabled: v });
    }),
  );
  on.append(li);
  root.append(on, voiceCard(), hotkeysCard(), fishCard(), musicCard(), grid(handsFreeCard(), sttCard()), customVoicesCard());
}

/* ═════════════ Чат ═════════════ */

type HistMsg = { role: string; text: string };
type Pend = { id: string; tool: string; args: Record<string, unknown> };
let chatBusy = false;

function chatBubble(msg: HistMsg, live = false): string {
  const who = msg.role === "user" ? "u" : "a";
  return `<div class="bub ${who}${live ? " live" : ""}"><div class="bub-text">${esc(msg.text) || "…"}</div></div>`;
}

export async function chatPage(root: HTMLElement): Promise<Refresher> {
  const [history, pendingAll, persona] = await Promise.all([
    fetchJson<HistMsg[]>("/api/history?limit=80").catch(() => [] as HistMsg[]),
    fetchJson<Pend[]>("/api/pending").catch(() => [] as Pend[]),
    fetchJson<PersonaView>("/api/persona").catch(() => null),
  ]);
  const name = persona?.profile.name || "Ая";
  const pending = pendingAll[0] || null;
  root.classList.add("page-chat");
  root.innerHTML = `
    <header class="page-head"><div class="kicker">чат</div><h1 class="page-title">Разговор с ${esc(name)}</h1></header>
    <div class="chat-panel">
      <div class="chat-log" id="chat-log">${history.map((m) => chatBubble(m)).join("") || `<div class="hint-box">Здесь будет ваша переписка.</div>`}</div>
      <div class="chat-confirm" id="chat-confirm"></div>
      <div class="chat-input">
        <textarea id="chat-text" rows="1" placeholder="Напиши ${esc(name)}… (Enter — отправить, Shift+Enter — перенос)"></textarea>
        <button class="send-btn" id="chat-send" aria-label="Отправить">${icon("i-send")}</button>
      </div>
    </div>`;
  const log = root.querySelector<HTMLElement>("#chat-log")!;
  log.scrollTop = log.scrollHeight;
  const area = root.querySelector<HTMLTextAreaElement>("#chat-text")!;
  const btn = root.querySelector<HTMLButtonElement>("#chat-send")!;
  area.addEventListener("input", () => {
    area.style.height = "auto";
    area.style.height = `${Math.min(160, area.scrollHeight)}px`;
  });

  const showConfirm = (p: Pend) => {
    const zone = root.querySelector<HTMLElement>("#chat-confirm")!;
    zone.innerHTML = `<div class="card confirm-card"><b>Подтверждение: ${esc(p.tool)}</b><pre class="mono">${esc(JSON.stringify(p.args, null, 1))}</pre><div class="confirm-btns"><button class="top-btn ok-btn" data-cid="${esc(p.id)}" data-ok="1">Разрешить</button><button class="top-btn no-btn" data-cid="${esc(p.id)}">Отказать</button></div></div>`;
    zone.querySelectorAll<HTMLButtonElement>("[data-cid]").forEach((b) =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const res = (await postJson("/api/action/confirm", { id: b.dataset.cid, approve: !!b.dataset.ok })) as { applied?: boolean; result?: string };
          zone.innerHTML = "";
          log.insertAdjacentHTML("beforeend", chatBubble({ role: "aya", text: res.applied ? res.result || "готово" : res.result || "отменено" }));
          log.scrollTop = log.scrollHeight;
        } finally {
          b.disabled = false;
        }
      }),
    );
  };
  if (pending) showConfirm(pending);

  const send = async () => {
    if (chatBusy) return;
    const text = area.value.trim();
    if (!text) return;
    chatBusy = true;
    btn.disabled = true;
    area.value = "";
    area.style.height = "auto";
    log.querySelector(".hint-box")?.remove();
    log.insertAdjacentHTML("beforeend", chatBubble({ role: "user", text }));
    const live = el("div", "bub a live");
    live.innerHTML = '<div class="bub-text"></div>';
    log.appendChild(live);
    log.scrollTop = log.scrollHeight;
    const target = live.querySelector(".bub-text")!;
    let got = "";
    try {
      const res = await fetch(`${CORE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const j = JSON.parse(line) as { delta?: string; done?: boolean; reply?: string; pending?: Pend; error?: string };
            if (j.delta) got += j.delta;
            if (j.done) {
              got = j.reply || got;
              if (j.error) got += `\n(ошибка: ${j.error})`;
              if (j.pending) showConfirm(j.pending);
            }
          } catch {
            /* неполная строка */
          }
        }
        target.textContent = got;
        log.scrollTop = log.scrollHeight;
      }
    } catch (e) {
      target.textContent = `ядро недоступно: ${String(e)}`;
    }
    live.classList.remove("live");
    chatBusy = false;
    btn.disabled = false;
    area.focus();
  };
  btn.addEventListener("click", () => void send());
  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void send();
    }
  });
  window.setTimeout(() => area.focus(), 50);
}

/* ═════════════ Память ═════════════ */

export async function memoryPage(root: HTMLElement): Promise<Refresher> {
  root.append(pageHead("память", "Что она помнит", "Дневник, факты о тебе и поиск по прошлым разговорам. Всё хранится только на этом ПК."));
  root.append(diaryCard(), grid(memoryCard(), memorySearchCard()), tasksCard());
}

/* ═════════════ Навыки ═════════════ */

export async function skillsPage(root: HTMLElement): Promise<Refresher> {
  root.append(pageHead("навыки", "Что ей можно делать", "Всё выключено по умолчанию. Опасные действия (команды, SSH, запись файлов) всегда просят подтверждение, если не включён доверенный режим."));
  root.append(grid(permsCard(), workspaceCard()), mcpCard());
}

/* ═════════════ Связи ═════════════ */

export async function linksPage(root: HTMLElement): Promise<Refresher> {
  root.append(pageHead("связи", "Мессенджеры", "Бот Telegram, твой личный аккаунт Telegram и Discord."));
  root.append(grid(tgCard(), discordCard()), tgUserCard());
}

/* ═════════════ Мозг ═════════════ */

export async function brainPage(root: HTMLElement): Promise<Refresher> {
  root.append(pageHead("мозг", "Модели", "Какие нейросети думают за Аю. Ключи хранятся только локально в ~/.aya/config.json."));
  root.append(
    llmCard("Основная модель", "разговоры; openai-совместимый API (OpenRouter и др.) или ollama", "llm"),
    grid(
      llmCard("Суб-агенты", "сложные поручения delegate; пусто = основная", "llm_agents"),
      llmCard("Зрение", "мультимодальная модель для screen_look; пусто = только OCR", "llm_vision"),
    ),
    gpuCard(),
  );
}

/* ═════════════ Игровой режим ═════════════ */

async function gameCard(): Promise<HTMLElement> {
  const ui = await fetchJson<{ game?: { on: boolean; app?: string; enabled?: boolean } }>("/api/ui").catch(() => ({}) as { game?: undefined });
  const g = ui.game ?? { on: false, enabled: true };
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Игровой режим"));
  const li = el("div", "set-item");
  const t = el("span", "grow");
  t.innerHTML =
    "Экономить ресурсы в играх" +
    `<small class="dim block">Пока на весь экран открыта игра (Valorant, CS2, Dota 2 и любая другая), Ая уходит под игру, ` +
    `не рисуется и не слушает «Ая, …», распознаёт речь лёгкой моделью на 2 ядрах, говорит облачным голосом и уступает процессор игре. ` +
    `Говорить с ней можно по клавише — ответит голосом.</small>` +
    (g.on ? `<small class="ok block">Сейчас: игра ${esc(g.app || "")}</small>` : "");
  li.append(
    t,
    switchEl(g.enabled !== false, async (v) => {
      await postJson("/api/config", { game_mode: v });
      toast(v ? "Игровой режим включён" : "Игровой режим выключен — в играх Ая работает как обычно");
    }),
  );
  card.append(li);
  return card;
}

/* ═════════════ Система ═════════════ */

export async function systemPage(root: HTMLElement): Promise<Refresher> {
  root.append(pageHead("система", "Состояние и журнал"));
  const again = el("div", "card");
  again.append(
    el("h3", "sec-title", "Первая настройка"),
    el("div", "hint-box", "Модель для ответов, голос, микрофон и имя — по шагам, как при первом запуске."),
    actionBtn("Пройти настройку заново", async () => {
      location.hash = "setup";
    }),
  );
  root.append(again, await gameCard());
  const metrics = el("div", "card-grid two");
  const audit = el("div", "card");
  const fill = async () => {
    let m: Record<string, unknown> = {};
    try {
      m = { ...(await fetchJson<Record<string, unknown>>("/api/state")), ...(await fetchJson<Record<string, unknown>>("/api/debug/metrics")) };
    } catch {}
    const eng = (m.engines as Record<string, boolean> | undefined) ?? {};
    const svc = (m.svc as Record<string, unknown> | undefined) ?? {};
    metrics.innerHTML = `
      <div class="card"><h3 class="sec-title">Состояние</h3>
        ${metricRow("настроение", RU_MOOD[String(m.mood)] || m.mood)}${metricRow("занятие", RU_ACT[String(m.activity)] || m.activity)}
        ${metricRow("энергия", m.energy)}${metricRow("внимание", m.attention === "user" ? "на тебе" : "своё")}</div>
      <div class="card"><h3 class="sec-title">Ресурсы</h3>
        ${metricRow("VRAM занято / всего, ГБ", m.vram_total_gb ? `${m.vram_allocated_gb} / ${m.vram_total_gb}` : "—")}
        ${metricRow("кэш озвучки, МБ", m.tts_cache_mb)}
        ${metricRow("LLM первый токен, мс", svc.llm_first_ms_avg)}
        ${metricRow("сбоев LLM", svc.llm_errors)}
        ${metricRow("в памяти", Object.entries(eng).filter(([, v]) => v).map(([k]) => k).join(", ") || "ничего")}</div>`;
    type Row = { ts: number; tool: string; result: string; allowed: boolean };
    const rows = await fetchJson<Row[]>("/api/audit").catch(() => [] as Row[]);
    audit.innerHTML = `<h3 class="sec-title">Журнал действий</h3>${
      rows.length
        ? `<table><thead><tr><th>когда</th><th>инструмент</th><th>итог</th><th></th></tr></thead><tbody>${rows
            .slice(0, 60)
            .map(
              (r) =>
                `<tr><td>${new Date(r.ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</td><td class="mono">${esc(r.tool)}</td><td>${esc(r.result)}</td><td class="${r.allowed ? "allowed" : "denied"}">${r.allowed ? "ok" : "отказ"}</td></tr>`,
            )
            .join("")}</tbody></table>`
        : `<div class="hint-box">Действий пока нет. Сюда пишется каждый запуск инструментов.</div>`
    }`;
  };
  await fill();
  root.append(metrics, grid(proactiveCard(), shellCard()), audit);
  return fill;
}

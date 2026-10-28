/* Карточки настроек консоли (перенесены из прежней консоли без изменения логики). */
import { clampFollowupSec } from "../followup";
import { buildSavePayload, buildServerPayload, formatArgs, formatEnv, type McpForm } from "../mcp-editor";
import { CORE, actionBtn, el, fetchJson, live, postJson, switchEl } from "./ui";

/* ── Настройки: полноценное управление ядром из браузера ── */

export const TOOLS: Record<string, string> = {
  open_app: "Открывать приложения и сайты",
  set_volume: "Управлять громкостью",
  create_note: "Создавать заметки",
  set_reminder: "Ставить напоминания",
  write_allowed_file: "Писать файлы в рабочей папке",
  search_allowed_folder: "Искать в разрешённой папке",
  get_task_status: "Смотреть статусы задач",
  read_allowed_file: "Читать файлы в рабочей папке",
  grep_folder: "Искать по содержимому кода",
  web_search: "Искать в интернете",
  web_open: "Открывать страницы сайтов",
  screen_look: "Смотреть на экран (скриншоты)",
  run_cmd: "Запускать команды (PowerShell)",
  ssh_run: "Команды на серверах (SSH)",
};

export const TTS_VOICES: { id: string; label: string; sub: string }[] = [
  { id: "f3", label: "Ая", sub: "аниме-эмоциональная · живой голос" },
  { id: "cute", label: "Света-синт", sub: "милый вариант · F5" },
  { id: "qwen_06", label: "Ая — Qwen 0.6B", sub: "тест · клон голоса · Qwen3-TTS · нужны локальные голоса" },
  { id: "qwen_17", label: "Ая — Qwen 1.7B", sub: "тест · клон голоса · Qwen3-TTS · нужны локальные голоса" },
  { id: "fish_miyuki", label: "Миюки (Lucky Star)", sub: "cloud · аниме · рус" },
  { id: "fish_rei", label: "Рей (Аянами)", sub: "cloud · тихая · аниме · рус" },
  { id: "fish_hoshino", label: "Хошино Ай", sub: "cloud · яркая поп-идол · рус" },
  { id: "fish_anime", label: "Аниме", sub: "cloud · озорная · рус" },
  { id: "fish_playful", label: "Игривый Девичий", sub: "cloud · нежная игривая · рус" },
  { id: "fish_mugi", label: "Муги (K-ON!)", sub: "cloud · мягкая тёплая · рус" },
];

export const VOICE_MODES: { id: string; label: string; sub: string }[] = [
  { id: "button", label: "Только кнопка", sub: "микрофон в главном окне по нажатию" },
  { id: "auto", label: "Активация по голосу", sub: "главное окно слушает понемногу" },
  { id: "hotkey", label: "Клавишей", sub: "зажать в любом окне и говорить (клавиша — ниже)" },
];





type LlmKey = "llm" | "llm_agents" | "llm_vision";

type LlmView = { type: string; base_url: string; model: string; api_key_set: boolean; api_key_tail: string };

export function llmCard(title: string, hint: string, key: LlmKey): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", title), el("div", "hint-box", hint));
  const grid = el("div", "form-grid");
  const ft = document.createElement("select");
  ft.className = "inp sel";
  for (const t of ["openai_compatible", "ollama"]) {
    const o = document.createElement("option");
    o.value = t;
    o.textContent = t === "ollama" ? "ollama" : "openai-совместимый";
    ft.append(o);
  }
  const fu = el("input", "inp") as HTMLInputElement;
  fu.placeholder = "base URL (например https://api.x…/v1)";
  const fm = el("input", "inp") as HTMLInputElement;
  fm.placeholder = "имя модели";
  const fk = el("input", "inp") as HTMLInputElement;
  fk.type = "password";
  fk.placeholder = "новый api-ключ (пусто = не менять)";
  grid.append(ft, fu, fm, fk);
  const status = el("div", "hint-box");
  const save = actionBtn("Сохранить", async () => {
    const body: Record<string, unknown> = { base_url: fu.value.trim(), model: fm.value.trim(), type: ft.value };
    if (fk.value.trim()) body.api_key = fk.value.trim();
    const res = await postJson("/api/llm", { [key]: body });
    const v = res[key] as LlmView | undefined;
    if (v) {
      fu.value = v.base_url || "";
      fm.value = v.model || "";
      ft.value = v.type === "ollama" ? "ollama" : "openai_compatible";
      fk.value = "";
      fk.placeholder = v.api_key_set ? `ключ: ${v.api_key_tail} (введи новый, чтобы заменить)` : "ключ не задан";
      status.textContent = "сохранено";
    } else status.textContent = "не удалось";
    window.setTimeout(() => (status.textContent = ""), 2500);
  });
  card.append(grid, save, status);
  void (async () => {
    try {
      const d = (await fetchJson<Record<string, LlmView>>("/api/llm"))[key];
      if (d) {
        fu.value = d.base_url || "";
        fm.value = d.model || "";
        ft.value = d.type === "ollama" ? "ollama" : "openai_compatible";
        fk.placeholder = d.api_key_set ? `ключ: ${d.api_key_tail} (введи новый, чтобы заменить)` : "ключ не задан";
      }
    } catch {
      status.textContent = "ядро не отвечает";
    }
  })();
  return card;
}

export function permsCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Разрешения инструментов"));

  const modeRow = el("div", "set-item");
  const modeLabel = el("span", "", "Доверенный режим (авто-подтверждение)");
  modeLabel.title = "Ая выполняет команды и записи сразу, без кнопки подтверждения";
  const modeSwitch = switchEl(false, async (v) => {
    await postJson("/api/config", { confirm_mode: v ? "auto" : "ask" });
  });
  modeRow.append(modeLabel, modeSwitch);
  card.append(modeRow);

  const bulkRow = el("div", "confirm-btns");
  const inputs: Record<string, HTMLInputElement> = {};
  const setAll = async (enabled: boolean) => {
    await postJson("/api/permissions/all", { enabled });
    try {
      const st = await fetchJson<Record<string, boolean>>("/api/permissions");
      for (const t of Object.keys(TOOLS)) if (inputs[t]) inputs[t].checked = !!st[t];
    } catch {}
  };
  bulkRow.append(actionBtn("Разрешить всё", () => setAll(true)), actionBtn("Отозвать всё", () => setAll(false)));
  card.append(bulkRow);

  const list = el("ul", "set-list");
  card.append(list);
  void (async () => {
    let state: Record<string, boolean> = {};
    try {
      state = await fetchJson<Record<string, boolean>>("/api/permissions");
    } catch {}
    for (const [tool, label] of Object.entries(TOOLS)) {
      const li = el("li", "set-item");
      const sw = switchEl(!!state[tool], async (v) => {
        await postJson("/api/permissions", { tool, enabled: v });
      });
      const inp = sw.querySelector("input");
      if (inp) inputs[tool] = inp as HTMLInputElement;
      li.append(el("span", "", label), sw);
      list.append(li);
    }
  })();
  void (async () => {
    try {
      const cfg = await fetchJson<{ confirm_mode?: string }>("/api/config");
      const inp = modeSwitch.querySelector("input") as HTMLInputElement | null;
      if (inp) inp.checked = cfg.confirm_mode === "auto";
    } catch {}
  })();
  return card;
}

export function workspaceCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Рабочая папка"));
  const hint = el("div", "hint-box", "Ая ищет и пишет файлы только здесь");
  const inp = el("input", "inp") as HTMLInputElement;
  inp.placeholder = "D:\\проекты\\заметки";
  inp.spellcheck = false;
  card.append(inp, actionBtn("Сохранить", async (b) => {
    const res = await postJson("/api/workspace", { folder: inp.value.trim() });
    hint.textContent = res.ok ? `Сохранено: ${res.folder}` : `Ошибка: ${String(res.error || "?")}`;
  }), hint);
  void (async () => {
    try {
      const d = await fetchJson<{ folder: string }>("/api/workspace");
      inp.value = d.folder || "";
    } catch {}
  })();
  return card;
}

export function memoryCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Память"));
  const list = el("ul", "set-list");
  const fk = el("input", "inp") as HTMLInputElement;
  fk.placeholder = "ключ (например: любимая музыка)";
  const fv = el("input", "inp") as HTMLInputElement;
  fv.placeholder = "значение";
  const load = async () => {
    list.innerHTML = "";
    let facts: { key: string; value: string }[] = [];
    try {
      facts = (await fetchJson<{ facts: { key: string; value: string }[] }>("/api/memory")).facts;
    } catch {}
    if (!facts.length) {
      list.append(el("li", "hint-box", "Пока ничего не запомнила"));
      return;
    }
    for (const f of facts) {
      const li = el("li", "set-item");
      li.append(el("span", "", `${f.key}: ${f.value}`), actionBtn("забыть", async () => {
        await postJson("/api/memory/forget", { key: f.key });
        await load();
      }));
      list.append(li);
    }
  };
  card.append(list, fk, fv, actionBtn("Запомнить", async () => {
    if (!fk.value.trim() || !fv.value.trim()) return;
    await postJson("/api/memory/add", { key: fk.value.trim(), value: fv.value.trim() });
    fk.value = "";
    fv.value = "";
    await load();
  }));
  void load();
  return card;
}

export function tasksCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Напоминания"));
  const list = el("ul", "set-list");
  const note = el("input", "inp") as HTMLInputElement;
  note.placeholder = "о чём напомнить";
  const mins = document.createElement("input");
  mins.type = "number";
  mins.min = "1";
  mins.max = "1440";
  (mins as HTMLInputElement).value = "30";
  mins.className = "inp tiny";
  const load = async () => {
    list.innerHTML = "";
    let tasks: { id: number; kind: string; due_at: number; status: string; result: string }[] = [];
    try {
      tasks = (await fetchJson<{ tasks: typeof tasks }>("/api/tasks")).tasks.filter(
        (t) => t.kind === "reminder" && (t.status === "pending" || t.status === "error"),
      );
    } catch {}
    if (!tasks.length) {
      list.append(el("li", "hint-box", "Напоминаний нет"));
      return;
    }
    for (const t of tasks) {
      const when = new Date(t.due_at * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      const li = el("li", "set-item");
      li.append(el("span", "mono", t.status === "error" ? "ошибка" : when), el("span", "grow", (t.result ? `${t.result} · ` : "") + `№${t.id}`), actionBtn("отмена", async () => {
        await postJson("/api/tasks/cancel", { id: t.id });
        await load();
      }));
      list.append(li);
    }
  };
  card.append(list, note, mins, actionBtn("Поставить", async () => {
    if (!note.value.trim()) return;
    await postJson("/api/tasks", { kind: "reminder", note: note.value.trim(), minutes: Number((mins as HTMLInputElement).value || 30) });
    note.value = "";
    await load();
  }));
  void load();
  return card;
}

type McpState = {
  command: string;
  args: string[];
  env?: Record<string, string>;
  cwd?: string;
  enabled: boolean;
  permitted: boolean;
  connected: boolean;
  error: string;
  tools: { key: string; name: string; description: string }[];
};

export function mcpCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "MCP-серверы (~/.aya/mcp.json)"));
  const list = el("ul", "set-list");
  const hint = el("div", "hint-box", "Сервер запускает команду на твоём ПК; секреты хранятся локально в открытом виде");
  const editor = el("div", "mcp-editor");
  editor.classList.add("hidden");
  let servers: Record<string, McpState> = {};
  let editing: string | null = null;

  const load = async () => {
    list.innerHTML = "";
    try {
      servers = await fetchJson<Record<string, McpState>>("/api/mcp");
    } catch {
      servers = {};
    }
    const names = Object.keys(servers);
    if (!names.length) {
      list.append(el("li", "hint-box", "Серверов нет — добавь первый"));
      return;
    }
    for (const [name, s] of Object.entries(servers)) {
      const li = el("li", "mcp-item");
      const head = el("div", "mcp-line");
      const dotCls = s.connected ? "ok" : s.enabled && s.error ? "err" : "off";
      const permSw = switchEl(s.permitted, async (v) => {
        const res = await postJson(`/api/mcp/perm/${encodeURIComponent(name)}`, { enabled: v });
        if (!res.ok) {
          hint.textContent = `не удалось изменить разрешение ${name}: ${String(res.detail || "ошибка")}`;
          (permSw.querySelector("input") as HTMLInputElement).checked = !v;
        }
      });
      head.append(
        el("span", "mcp-name", name),
        el("span", "dim", s.connected ? `${s.tools.length} инстр.` : ""),
        permSw,
      );
      const sub = el("div", "mcp-sub");
      sub.textContent = s.connected
        ? s.tools.map((t) => t.name).join(", ") || "нет инструментов"
        : s.error
          ? `ошибка: ${s.error}`
          : "выключен";
      const controls = el("div", "mcp-line");
      controls.append(
        actionBtn("изменить", async () => openEditor(name)),
        actionBtn("проверить", async () => {
          const res = await postJson("/api/mcp/test", {
            name,
            command: s.command,
            args: s.args,
            env: s.env || {},
            cwd: s.cwd || "",
            enabled: s.enabled,
          });
          sub.textContent = res.ok
            ? `инструменты: ${(res.tools as { name: string }[] | undefined)?.map((t) => t.name).join(", ") || "нет"}`
            : `ошибка: ${String(res.detail || res.error || "не удалось")}`;
        }),
        actionBtn("удалить", async () => {
          if (!confirm(`Удалить сервер ${name}?`)) return;
          const res = await postJson("/api/mcp/delete", { name });
          if (!res.ok) {
            hint.textContent = `не удалось удалить ${name}: ${String(res.detail || "ошибка")}`;
            return;
          }
          if (editing === name) closeEditor();
          await load();
        }),
        actionBtn(s.enabled ? "выключить" : "включить", async () => {
          const res = await postJson("/api/mcp/toggle", { server: name });
          if (!res.ok) {
            hint.textContent = `не удалось переключить ${name}: ${String(res.detail || "ошибка")}`;
            return;
          }
          await load();
        }),
      );
      head.querySelector(".switch")?.before(el("em", `dot ${dotCls}`));
      li.append(head, sub, controls);
      list.append(li);
    }
  };

  function closeEditor() {
    editing = null;
    editor.classList.add("hidden");
    editor.innerHTML = "";
  }

  function openEditor(name: string | null) {
    editing = name;
    const s = name ? servers[name] : undefined;
    editor.innerHTML = "";
    editor.classList.remove("hidden");

    const nameIn = el("input", "inp") as HTMLInputElement;
    nameIn.placeholder = "имя: латиница, цифры, дефис";
    nameIn.value = name || "";
    const commandIn = el("input", "inp") as HTMLInputElement;
    commandIn.placeholder = "команда: npx, python, cmd…";
    commandIn.value = s?.command || "";
    const argsIn = document.createElement("textarea");
    argsIn.className = "inp";
    argsIn.placeholder = "аргументы — по одному в строке";
    argsIn.value = formatArgs(s?.args || []);
    const envIn = document.createElement("textarea");
    envIn.className = "inp";
    envIn.placeholder = "env — KEY=VALUE, по одному в строке";
    envIn.value = formatEnv(s?.env || {});
    const cwdIn = el("input", "inp") as HTMLInputElement;
    cwdIn.placeholder = "рабочая папка (необязательно)";
    cwdIn.value = s?.cwd || "";
    const enabledIn = document.createElement("input");
    enabledIn.type = "checkbox";
    enabledIn.checked = !!s?.enabled;
    const sw = el("label", "switch");
    sw.append(enabledIn, el("span", "slider"));
    const status = el("div", "hint-box");
    const actions = el("div", "mcp-editor-actions");

    const form = (): McpForm => ({
      name: nameIn.value,
      command: commandIn.value,
      argsText: argsIn.value,
      envText: envIn.value,
      cwd: cwdIn.value,
      enabled: enabledIn.checked,
    });

    actions.append(
      actionBtn("сохранить", async () => {
        try {
          const payload = buildSavePayload(form(), editing || "");
          const res = await postJson("/api/mcp/save", payload);
          if (!res.ok) {
            status.textContent = String(res.detail || "не удалось сохранить");
            return;
          }
          editing = payload.name;
          status.textContent = res.error ? `сохранено, но не запустился: ${String(res.error)}` : "сохранено";
          await load();
          if (!res.error) closeEditor();
        } catch (e) {
          status.textContent = e instanceof Error ? e.message : String(e);
        }
      }),
      actionBtn("проверить", async () => {
        try {
          const res = await postJson("/api/mcp/test", buildServerPayload(form()));
          if (!res.ok) {
            status.textContent = `ошибка: ${String(res.detail || res.error || "не удалось")}`;
            return;
          }
          const tools = (res.tools as { name: string; description: string }[] | undefined) || [];
          status.textContent = tools.length
            ? `инструменты: ${tools.map((t) => `${t.name}${t.description ? ` — ${t.description}` : ""}`).join(", ")}`
            : "сервер ответил, инструментов нет";
        } catch (e) {
          status.textContent = e instanceof Error ? e.message : String(e);
        }
      }),
      actionBtn("отмена", async () => closeEditor()),
    );

    if (editing) {
      actions.append(actionBtn("удалить", async () => {
        if (!confirm(`Удалить сервер ${editing}?`)) return;
        const res = await postJson("/api/mcp/delete", { name: editing });
        if (res.ok) {
          closeEditor();
          await load();
        }
      }));
    }

    const enabledRow = el("div", "seg-row");
    enabledRow.append(sw, el("span", "", "включён"));
    editor.append(nameIn, commandIn, argsIn, envIn, cwdIn, enabledRow, actions, status);
  }

  card.append(list, actionBtn("Добавить сервер", async () => openEditor(null)), editor, hint);
  void load();
  return card;
}

export function tgCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Мост Telegram"));
  const tok = el("input", "inp") as HTMLInputElement;
  tok.type = "password";
  tok.placeholder = "токен бота (123456:ABC-DEF…)";
  const ch = el("input", "inp") as HTMLInputElement;
  ch.placeholder = "твой chat_id (число)";
  const en = document.createElement("input");
  en.type = "checkbox";
  const sw = el("label", "switch");
  sw.append(en, el("span", "slider"));
  const status = el("div", "hint-box");
  card.append(tok, ch, sw, el("span", "", "включён"), actionBtn("Сохранить и применить", async () => {
    const body: Record<string, unknown> = { chat_id: ch.value.trim(), enabled: en.checked };
    if (tok.value.trim()) body.token = tok.value.trim();
    const s = await postJson("/api/telegram", body);
    tok.value = "";
    renderTgStatus(s, status);
  }), status);
  const renderTgStatus = (s: Record<string, unknown> | null, target: HTMLElement) => {
    if (!s) {
      target.textContent = "ядро не отвечает";
      return;
    }
    const bits: string[] = [];
    if (s.running) bits.push("мост работает");
    else if (s.enabled) bits.push(s.error ? `не запустился: ${s.error}` : "включён, ещё поднимается");
    else bits.push("выключен");
    if (s.has_token) bits.push("токен задан");
    if (s.received || s.sent) bits.push(`вход ${s.received} / исх ${s.sent}`);
    target.textContent = bits.join(" • ");
  };
  void (async () => {
    try {
      const s = await fetchJson<Record<string, unknown>>("/api/telegram");
      ch.value = String(s.chat_id || "");
      en.checked = !!s.enabled;
      renderTgStatus(s, status);
    } catch {
      status.textContent = "ядро не отвечает";
    }
  })();
  return card;
}

type AgentState = { name: string; preview: string };

export function discordCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Мост Discord"));
  const tok = el("input", "inp") as HTMLInputElement;
  tok.type = "password";
  tok.placeholder = "токен бота (из Developer Portal)";
  const ch = el("input", "inp") as HTMLInputElement;
  ch.placeholder = "ID канала (число, ПКМ → «копировать ID»)";
  const en = document.createElement("input");
  en.type = "checkbox";
  const sw = el("label", "switch");
  sw.append(en, el("span", "slider"));
  const status = el("div", "hint-box", "в Developer Portal бота нужен MESSAGE CONTENT INTENT");
  card.append(tok, ch, sw, el("span", "", "включён"), actionBtn("Сохранить и применить", async () => {
    const body: Record<string, unknown> = { channel_id: ch.value.trim(), enabled: en.checked };
    if (tok.value.trim()) body.token = tok.value.trim();
    const s = await postJson("/api/discord", body);
    tok.value = "";
    renderDiscordStatus(s, status);
  }), status);
  const renderDiscordStatus = (s: Record<string, unknown> | null, target: HTMLElement) => {
    if (!s) {
      target.textContent = "ядро не отвечает";
      return;
    }
    const bits: string[] = [];
    if (s.running) bits.push("мост работает");
    else if (s.enabled) bits.push(s.error ? `не запустился: ${s.error}` : "включён, ещё поднимается");
    else bits.push("выключен");
    if (s.has_token) bits.push("токен задан");
    if (s.package === false) bits.push("нет пакета discord.py");
    if (s.received || s.sent) bits.push(`вход ${s.received} / исх ${s.sent}`);
    target.textContent = bits.join(" • ");
  };
  void (async () => {
    try {
      const s = await fetchJson<Record<string, unknown>>("/api/discord");
      ch.value = String(s.channel_id || "");
      en.checked = !!s.enabled;
      renderDiscordStatus(s, status);
    } catch {
      status.textContent = "ядро не отвечает";
    }
  })();
  return card;
}

export function proactiveCard(): HTMLElement {
  const card = el("div", "card");
  card.append(
    el("h3", "sec-title", "Проактивность"),
    el(
      "div",
      "hint-box",
      "Ая следит за активными окнами (только заголовки, без содержимого) и чувствует твой день: утренний привет, зов на перерыв, вечерний дайджест. «Тишина» глушит инициативы — напоминания работают всегда.",
    ),
  );
  const enabledSw = switchEl(false, async (v) => void (await postJson("/api/proactive", { enabled: v })));
  const focusSw = switchEl(false, async (v) => void (await postJson("/api/proactive", { focus: v })));
  const digestSw = switchEl(false, async (v) => void (await postJson("/api/proactive", { digest: v })));
  live.focusInput = focusSw.querySelector("input") as HTMLInputElement;
  const grid = el("div", "form-grid");
  grid.append(
    el("span", "", "инициативы"),
    enabledSw,
    el("span", "", "тишина (не отвлекать)"),
    focusSw,
    el("span", "", "вечерний дайджест"),
    digestSw,
  );
  card.append(grid);
  const status = el("div", "hint-box");
  card.append(status);
  void (async () => {
    try {
      const s = await fetchJson<Record<string, unknown>>("/api/proactive");
      (enabledSw.querySelector("input") as HTMLInputElement).checked = !!s.enabled;
      (focusSw.querySelector("input") as HTMLInputElement).checked = !!s.focus;
      (digestSw.querySelector("input") as HTMLInputElement).checked = !!s.digest;
      const now = (s.now as Record<string, unknown>) || {};
      const tops = (s.top as Array<[string, unknown]>) || [];
      const bits: string[] = [];
      if (now.app) bits.push(`сейчас: ${String(now.app)}`);
      const minutesTop = tops
        .slice(0, 4)
        .map(([app, mins]) => `${String(app)} ${String(mins)}м`)
        .join(", ");
      if (minutesTop) bits.push(`топ дня: ${minutesTop}`);
      status.textContent = bits.join(" • ") || "наблюдение за активностью ещё не началось";
    } catch {
      status.textContent = "ядро не отвечает";
    }
  })();
  return card;
}

type TgLoginState = "idle" | "code_sent" | "password_needed" | "done" | "error";

let tgPollTimer: number | null = null;

export function tgUserCard(): HTMLElement {
  const card = el("div", "card");
  card.append(
    el("h3", "sec-title", "Telegram — твой аккаунт"),
    el(
      "div",
      "hint-box",
      "Ая работает с твоим личным Telegram: рассказывает о личных сообщениях и может отвечать от твоего имени. api_id/api_hash — на my.telegram.org → «API development tools». Сессия хранится локально, только у тебя.",
    ),
  );
  const apiId = el("input", "inp") as HTMLInputElement;
  apiId.placeholder = "api_id (число)";
  const apiHash = el("input", "inp") as HTMLInputElement;
  apiHash.type = "password";
  apiHash.placeholder = "api_hash";
  const phone = el("input", "inp") as HTMLInputElement;
  phone.placeholder = "телефон (+7…)";
  const codeInp = el("input", "inp") as HTMLInputElement;
  codeInp.placeholder = "код из Telegram";
  const pwInp = el("input", "inp") as HTMLInputElement;
  pwInp.type = "password";
  pwInp.placeholder = "пароль 2FA (если включена)";
  // доступ до Telegram через блокировки: MTProto-прокси или локальный socks5 (Karing/v2ray)
  const proxySel = document.createElement("select");
  proxySel.className = "inp sel";
  for (const [v, t] of [
    ["", "без прокси"],
    ["mtproto", "MTProto-прокси"],
    ["socks5", "socks5 (Karing и т.п.)"],
  ] as const) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t;
    proxySel.append(o);
  }
  const proxyHost = el("input", "inp") as HTMLInputElement;
  proxyHost.placeholder = "прокси: хост";
  const proxyPort = el("input", "inp") as HTMLInputElement;
  proxyPort.placeholder = "прокси: порт";
  const proxySecret = el("input", "inp") as HTMLInputElement;
  proxySecret.placeholder = "секрет MTProto (ee…/dd… или base64)";
  const modeSel = document.createElement("select");
  modeSel.className = "inp sel";
  for (const [v, t] of [
    ["report", "доклады — рассказывает, ответ пишешь сам"],
    ["draft", "черновики — ответ после твоего подтверждения"],
    ["auto", "автопилот — отвечает сама"],
  ] as const) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t;
    modeSel.append(o);
  }
  const contacts = el("input", "inp") as HTMLInputElement;
  contacts.placeholder = "контакты через запятую (пусто = вся личка)";
  const privInput = document.createElement("input");
  privInput.type = "checkbox";
  const privSw = el("label", "switch");
  privSw.append(privInput, el("span", "slider"));
  const enInput = document.createElement("input");
  enInput.type = "checkbox";
  const enSw = el("label", "switch");
  enSw.append(enInput, el("span", "slider"));
  const status = el("div", "hint-box");

  const credsGrid = el("div", "form-grid");
  credsGrid.append(apiId, apiHash, phone);
  card.append(credsGrid, actionBtn("Получить код", async () => {
    if (!apiId.value.trim() || !apiHash.value.trim() || !phone.value.trim()) {
      status.textContent = "заполни api_id, api_hash и телефон";
      return;
    }
    const pv = proxyValue();
    if (proxySel.value && !Object.keys(pv).length) {
      status.textContent = "заполни хост/порт прокси (и секрет для MTProto)";
      return;
    }
    const res = await postJson("/api/tguser/login", {
      api_id: Number(apiId.value.trim()),
      api_hash: apiHash.value.trim(),
      phone: phone.value.trim(),
      proxy: proxySel.value ? pv : null, // тип не выбран — прокси не трогаем (сохранённый остаётся)
    });
    loginState(String(res.state || "error"), String(res.error || res.hint || ""));
  }));
  card.append(codeInp, actionBtn("Войти по коду", async () => {
    const res = await postJson("/api/tguser/code", { code: codeInp.value.trim() });
    loginState(String(res.state || "error"), String(res.error || res.hint || ""));
  }));
  card.append(pwInp, actionBtn("Подтвердить 2FA", async () => {
    const res = await postJson("/api/tguser/password", { password: pwInp.value });
    loginState(String(res.state || "error"), String(res.error || res.hint || ""));
  }));

  const saveGrid = el("div", "form-grid");
  saveGrid.append(
    el("span", "", "режим"),
    modeSel,
    el("span", "", "только личные чаты"),
    privSw,
    el("span", "", "включён"),
    enSw,
  );
  const proxyValue = (): Record<string, unknown> => {
    const host = proxyHost.value.trim();
    const port = Number(proxyPort.value.trim());
    const secret = proxySecret.value.trim();
    if (proxySel.value === "socks5" && host && port > 0) return { type: "socks5", host, port, secret: "" };
    if (proxySel.value === "mtproto" && host && port > 0 && secret) return { type: "mtproto", host, port, secret };
    return {};
  };
  const proxyGrid = el("div", "form-grid");
  proxyGrid.append(proxySel, proxyHost, proxyPort, proxySecret);
  const proxyHint = el(
    "div",
    "hint-box",
    "Прокси нужен, если провайдер режет Telegram: MTProto — хост/порт/секрет из свежего публичного списка (mtpro.xyz), socks5 — включи Karing и укажи его локальный порт.",
  );
  card.append(
    proxyGrid,
    proxyHint,
    contacts,
    saveGrid,
    actionBtn("Сохранить и применить", async () => {
      const s = await postJson("/api/tguser", {
        enabled: enInput.checked,
        mode: modeSel.value,
        contacts: contacts.value.split(",").map((x) => x.trim()).filter(Boolean),
        private_only: privInput.checked,
        proxy: proxyValue(),
      });
      renderStatus(s);
    }),
    actionBtn("Выйти из аккаунта", async () => {
      const s = await postJson("/api/tguser/logout", {});
      renderStatus(s);
    }),
    status,
  );

  const loginState = (st: string, note: string) => {
    const s = st as TgLoginState;
    let text = note;
    if (s === "code_sent") text = "код отправлен в Telegram — введи его тут";
    else if (s === "password_needed") text = "включена 2FA — введи пароль";
    else if (s === "done") text = "вход выполнен — сессия сохранена";
    else if (s === "error") text = `не получилось: ${note}`;
    status.textContent = text;
  };
  const renderStatus = (s: Record<string, unknown>) => {
    const bits: string[] = [];
    if (s.running) bits.push("подключено к моему аккаунту");
    else if (s.has_session) bits.push(s.error ? `вход есть, не подключилось: ${s.error}` : "сессия есть, ещё подключаюсь");
    else bits.push("сессии нет — пройди вход");
    if (s.phone_tail) bits.push(`аккаунт ${String(s.phone_tail)}`);
    const p = s.proxy as { type?: string; host?: string; port?: number | string } | undefined;
    if (p && p.type) bits.push(`через ${String(p.type)} ${String(p.host)}:${String(p.port)}`);
    if (s.has_session) bits.push(`режим: ${String(s.mode)}`);
    if (s.package === false) bits.push("нет пакета telethon");
    if (s.received || s.sent) bits.push(`вход ${String(s.received)} / исх ${String(s.sent)}`);
    status.textContent = bits.join(" • ");
  };
  // статус обновляем сами (только строка статуса, поля ввода не трогаем)
  const pollStatus = async (initial: boolean) => {
    // ушли со страницы — перестаём опрашивать (раньше таймер жил вечно)
    if (!initial && !card.isConnected) {
      if (tgPollTimer !== null) clearInterval(tgPollTimer);
      tgPollTimer = null;
      return;
    }
    try {
      const s = await fetchJson<Record<string, unknown>>("/api/tguser");
      if (initial) {
        modeSel.value = String(s.mode || "report");
        contacts.value = Array.isArray(s.contacts) ? (s.contacts as string[]).join(", ") : "";
        privInput.checked = !!s.private_only;
        enInput.checked = !!s.enabled;
        if (s.phone_tail) phone.placeholder = `аккаунт ${String(s.phone_tail)}`;
        const p = s.proxy as { type?: string; host?: string; port?: number | string; secret?: string } | undefined;
        if (p && p.type) {
          proxySel.value = String(p.type);
          proxyHost.value = String(p.host || "");
          proxyPort.value = String(p.port ?? "");
          proxySecret.value = String(p.secret || "");
        }
      }
      const st = String(s.login_state || "idle");
      if (st === "code_sent") status.textContent = "код отправлен — введи его в поле выше";
      else if (st === "password_needed") status.textContent = "введи пароль 2FA выше";
      else renderStatus(s);
    } catch {
      status.textContent = "ядро не отвечает";
    }
  };
  if (tgPollTimer !== null) clearInterval(tgPollTimer);
  tgPollTimer = window.setInterval(() => void pollStatus(false), 5000);
  void pollStatus(true);
  return card;
}

type AutostartApi = { enable: () => Promise<void>; disable: () => Promise<void>; isEnabled: () => Promise<boolean> };

export function shellCard(): HTMLElement {
  const card = el("div", "card");
  card.append(
    el("h3", "sec-title", "Оболочка"),
    el(
      "div",
      "hint-box",
      "Глобальный хоткей Alt+Shift+A — показать/скрыть Аю из любого приложения. Ая также живёт в трее (рядом с часами): показать себя, открыть консоль, выйти.",
    ),
  );
  const status = el("div", "hint-box");
  const autoSw = switchEl(false, async (v) => {
    try {
      const api = (await import("@tauri-apps/plugin-autostart")) as unknown as AutostartApi;
      if (v) await api.enable();
      else await api.disable();
      status.textContent = v ? "автозапуск включён" : "автозапуск выключен";
    } catch {
      status.textContent = "автозапуск доступен только внутри приложения Aya";
      (autoSw.querySelector("input") as HTMLInputElement).checked = !v;
    }
  });
  const grid = el("div", "form-grid");
  grid.append(el("span", "", "запускать с Windows"), autoSw);
  card.append(grid, status);
  void (async () => {
    try {
      const api = (await import("@tauri-apps/plugin-autostart")) as unknown as AutostartApi;
      status.textContent = (await api.isEnabled()) ? "автозапуск включён" : "автозапуск выключен";
    } catch {
      status.textContent = "внутри Aya: хоткей Alt+Shift+A, трей у часов";
    }
  })();
  return card;
}

export function agentsCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Личности (агенты)"));
  const list = el("ul", "set-list");
  const newName = el("input", "inp") as HTMLInputElement;
  newName.placeholder = "имя новой личности (SOUL.md в ~/.aya/agents)";
  const soulEdit = el("textarea", "soul-edit") as HTMLTextAreaElement;
  soulEdit.rows = 5;
  soulEdit.placeholder = "SOUL.md активной личности — характер, манера, специализация";
  const hint = el("div", "hint-box");
  let activeName = "";
  let activeIsMain = true;
  const load = async () => {
    list.innerHTML = "";
    let s: { active: string; agents: AgentState[] } | null = null;
    try {
      s = await fetchJson<{ active: string; agents: AgentState[] }>("/api/agents");
    } catch {}
    if (!s) {
      hint.textContent = "ядро не отвечает";
      return;
    }
    const mk = (label: string, isActive: boolean, selName: string, isMain = false) => {
      const li = el("li", "set-item");
      const b = el("button", "pill" + (isActive ? " active" : ""), label) as HTMLButtonElement;
      b.addEventListener("click", async () => {
        await postJson("/api/agents/select", { name: isActive ? "" : selName });
        await load();
        await pickSoul(selName, isActive, isMain);
      });
      li.append(b, switchEl(isActive, async () => {
        await postJson("/api/agents/select", { name: isActive ? "" : selName });
        await pickSoul(selName, !isActive, isMain);
        await load();
      }));
      list.append(li);
    };
    mk("Aya (основная)", s.active === "", "", true);
    for (const a of s.agents) mk(a.name, s.active === a.name, a.name);
    hint.textContent = s.active ? `активна личность: ${s.active}` : "";
    await pickSoul(s.active || "", !!s.active, s.active === "");
  };
  const pickSoul = async (name: string, isActive: boolean, isMain = false) => {
    activeName = name;
    activeIsMain = isMain;
    if (!isActive || !name) {
      soulEdit.value = "";
      soulEdit.placeholder = isMain ? "основная душа: ~/.aya/SOUL.md (редактируется через файл)" : "выбери личность, чтобы редактировать её SOUL.md";
      return;
    }
    try {
      const d = await fetchJson<{ soul: string }>(`/api/agents/soul/${encodeURIComponent(name)}`);
      soulEdit.value = d.soul || "";
      soulEdit.placeholder = `SOUL.md: ${name}`;
    } catch {
      soulEdit.value = "";
    }
  };
  const saveSoul = () => actionBtn("Сохранить характер", async () => {
    if (!activeName || !soulEdit.value.trim()) return;
    const res = await postJson(`/api/agents/soul/${encodeURIComponent(activeName)}`, { name: activeName, soul: soulEdit.value });
    hint.textContent = res.ok ? "характер сохранён" : String(res.message || "не удалось");
    window.setTimeout(() => (hint.textContent = activeName ? `активна личность: ${activeName}` : ""), 2500);
  });
  card.append(list, newListElement(), soulEdit, saveSoul(), hint, actionBtn("Создать личность", async () => {
    if (!newName.value.trim()) return;
    const res = await postJson("/api/agents/create", { name: newName.value.trim() });
    hint.textContent = res.ok ? "создано" : String(res.message || "не удалось");
    newName.value = "";
    await load();
  }));
  function newListElement() {
    return newName;
  }
  void load();
  return card;
}

export function voiceCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Озвучка и голос"));
  const voices = el("ul", "voice-choice");
  const vol = document.createElement("input");
  vol.type = "range";
  vol.min = "0";
  vol.max = "300";
  vol.step = "5";
  vol.value = "100";
  vol.className = "vol-range";
  const volVal = el("span", "mono", "100%");
  let volT = 0;
  vol.addEventListener("input", () => {
    volVal.textContent = `${vol.value}%`;
    window.clearTimeout(volT);
    volT = window.setTimeout(async () => {
      await postJson("/api/config", { tts_volume: Number(vol.value) });
    }, 350);
  });
  const vmWrap = el("div", "seg-row");
  const initWrap = el("div", "seg-row");
  const fuWrap = el("div", "seg-row");
  const fuSwitch = switchEl(false, async (v) => {
    await postJson("/api/config", { followup: v });
  });
  const fuInput = fuSwitch.querySelector("input") as HTMLInputElement;
  const fuSec = document.createElement("input");
  fuSec.type = "number";
  fuSec.min = "2";
  fuSec.max = "30";
  fuSec.className = "inp tiny";
  fuSec.value = "6";
  fuSec.title = "сколько секунд слушать после ответа";
  fuSec.addEventListener("change", async () => {
    const n = clampFollowupSec(Number(fuSec.value));
    fuSec.value = String(n);
    await postJson("/api/config", { followup_sec: n });
  });
  fuWrap.append(fuSwitch, el("span", "", "свободные руки"), fuSec, el("span", "dim", "сек"));
  const fuHint = el("div", "hint-box", "после голосового ответа слушать ещё несколько секунд; в режиме «авто» не нужно");
  live.followupInput = fuInput;
  live.followupSec = fuSec;
  const syncFollowupAvailability = (mode: string) => {
    const auto = mode === "auto";
    fuInput.disabled = auto;
    fuSec.disabled = auto;
    fuWrap.classList.toggle("disabled", auto);
  };
  card.append(
    el("div", "hint-box", "голос для озвучки"), voices, vol, volVal,
    el("div", "hint-box", "микрофон в главном окне"), vmWrap,
    el("div", "hint-box", "Ая сама заводит разговор"), initWrap,
    el("div", "hint-box", "свободные руки"), fuWrap, fuHint,
  );
  let curVoice = "fish_miyuki";
  let voiceList: { id: string; label: string; sub: string }[] = [...TTS_VOICES];
  const renderVoices = (list: HTMLElement) => {
    list.innerHTML = "";
    for (const v of voiceList) {
      const li = el("li", "opt" + (v.id === curVoice ? " active" : ""));
      li.append(el("span", "opt-label", v.label), el("span", "opt-sub", v.sub), el("span", "opt-check", v.id === curVoice ? "✓" : ""));
      li.addEventListener("click", async () => {
        if (v.id === curVoice) return;
        curVoice = v.id;
        renderVoices(list);
        await postJson("/api/config", { voice_id: v.id });
      });
      list.append(li);
    }
  };
  const renderModes = (wrap: HTMLElement, mode: string) => {
    wrap.innerHTML = "";
    syncFollowupAvailability(mode);
    for (const m of VOICE_MODES) {
      const b = el("button", "seg" + (m.id === mode ? " active" : ""), m.label) as HTMLButtonElement;
      b.title = m.sub;
      b.addEventListener("click", async () => {
        if (m.id === mode) return;
        await postJson("/api/config", { voice_mode: m.id });
        renderModes(wrap, m.id);
      });
      wrap.append(b);
    }
  };
  const renderInit = (wrap: HTMLElement, on: boolean) => {
    wrap.innerHTML = "";
    const yes = el("button", "seg" + (on ? " active" : ""), "да") as HTMLButtonElement;
    const no = el("button", "seg" + (on ? "" : " active"), "нет") as HTMLButtonElement;
    yes.addEventListener("click", async () => {
      if (on) return;
      await postJson("/api/config", { initiative: true });
      renderInit(wrap, true);
    });
    no.addEventListener("click", async () => {
      if (!on) return;
      await postJson("/api/config", { initiative: false });
      renderInit(wrap, false);
    });
    wrap.append(yes, no);
  };
  void (async () => {
    try {
      const cfg = await fetchJson<{
        tts_voice_id: string;
        tts_volume: number;
        voice_mode: string;
        initiative: boolean;
        followup?: boolean;
        followup_sec?: number;
      }>("/api/config");
      curVoice = cfg.tts_voice_id || "fish_miyuki";
      try {
        const own = await fetchJson<{ custom: { id: string; label: string; sub: string }[] }>("/api/voices");
        voiceList = [...(own.custom || []), ...TTS_VOICES];
      } catch {}
      vol.value = String(cfg.tts_volume ?? 100);
      volVal.textContent = `${vol.value}%`;
      renderVoices(voices);
      renderModes(vmWrap, cfg.voice_mode || "button");
      renderInit(initWrap, cfg.initiative ?? true);
      fuInput.checked = !!cfg.followup;
      fuSec.value = String(clampFollowupSec(cfg.followup_sec ?? 6));
    } catch {
      voices.append(el("li", "hint-box", "ядро не отвечает"));
    }
  })();
  return card;
}

export function gpuCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Память GPU"));
  const sel = document.createElement("select");
  sel.className = "inp sel";
  for (const [v, l] of [["0", "не выгружать"], ["3", "3 мин"], ["5", "5 мин"], ["10", "10 мин"], ["15", "15 мин"]] as const) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = l;
    sel.append(o);
  }
  sel.addEventListener("change", async () => {
    await postJson("/api/gpu", { minutes: Number(sel.value) });
  });
  const info = el("div", "hint-box");
  const load = async () => {
    try {
      const d = await fetchJson<{ idle_min: number; engines: Record<string, boolean>; "touched"?: Record<string, { idle_s: number }> }>("/api/gpu");
      sel.value = String(d.idle_min ?? 5);
      const eng = d.engines || {};
      const rows = Object.entries(eng).map(([k, v]) => `${k}: ${v ? "в памяти" : "выгружена"}`);
      info.textContent = rows.join(" · ") || "нет загруженных моделей";
    } catch {
      info.textContent = "ядро не отвечает";
    }
  };
  card.append(sel, actionBtn("Освободить сейчас", async () => {
    await postJson("/api/debug/unload", {});
    await load();
  }), info);
  void load();
  return card;
}


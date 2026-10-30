/* Мастер первого запуска: модель для ответов → голос → микрофон → кто она → готово.
   Каждый шаг сразу заполнен значениями по умолчанию; загрузки идут в фоне;
   любой необязательный шаг можно пропустить. Шаг запоминается в ядре. */
import { hotkeyField } from "./hotkey-field";
import { TTS_VOICES } from "./cards";
import { STEPS, errorText, llmKindOf, llmPreset, modelOptions, type LlmKind } from "./setup-logic";
import { CORE, el, esc, fetchJson, icon, postJson, toast } from "./ui";

type SetupState = { done: boolean; step: number; llm: { type: string; base_url: string; model: string; api_key_set: boolean }; fish_key_set: boolean };
type PackState = { state: "none" | "running" | "done" | "error"; error: string; size_mb: number; title: string };
type Packs = { packs: Record<string, PackState>; whisper: { state: string; model: string }; gpu: boolean };
type ModelInfo = { active: boolean; file: string };

const FISH = TTS_VOICES.filter((v) => v.id.startsWith("fish_"));
const GREETING = "Привет! Я Ая. Зови меня, когда захочешь поговорить.";
let open = false;

function gb(mb: number) {
  return mb >= 1000 ? `~${(mb / 1000).toFixed(1).replace(".", ",")} ГБ` : `~${mb} МБ`;
}

async function inApp<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null; // консоль открыта в браузере
  }
}

export async function openSetup(): Promise<void> {
  if (open) return;
  open = true;
  let st: SetupState;
  try {
    st = await fetchJson<SetupState>("/api/setup");
  } catch {
    open = false;
    toast("Ядро ещё просыпается — открой настройку через пару секунд");
    return;
  }
  let step = Math.min(st.step, STEPS.length - 1);
  let llmOk = st.llm.api_key_set || /127\.0\.0\.1|localhost/.test(st.llm.base_url);
  void postJson("/api/setup/whisper/prefetch", {}).catch(() => {}); // Whisper качается, пока человек проходит шаги

  const back = el("div", "wiz-backdrop setup-backdrop");
  const box = el("div", "wiz setup");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.innerHTML = `
    <header class="wiz-head">
      <div>
        <div class="kicker">первый запуск</div>
        <h2 class="wiz-title">Настроим Аю</h2>
      </div>
    </header>
    <div class="wiz-body">
      <nav class="wiz-steps">${STEPS.map((s, i) => `<button data-step="${i}"><i>${i + 1}</i>${s}</button>`).join("")}</nav>
      <div class="wiz-main"><div class="wiz-content" id="setup-content"></div></div>
    </div>
    <footer class="wiz-foot">
      <button class="btn ghost" id="setup-back">Назад</button>
      <span class="wiz-count" id="setup-count"></span>
      <button class="btn grad" id="setup-next">Дальше ${icon("i-arrow")}</button>
    </footer>`;
  back.append(box);
  document.body.append(back);
  requestAnimationFrame(() => back.classList.add("show"));
  const q = <T extends HTMLElement>(sel: string) => box.querySelector<T>(sel)!;
  const content = q("#setup-content");
  const nextBtn = q<HTMLButtonElement>("#setup-next");
  let cleanup: (() => void) | null = null;

  const canNext = () => step !== 1 || llmOk;
  const refreshNav = () => {
    box.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b) => {
      const i = Number(b.dataset.step);
      b.classList.toggle("active", i === step);
      b.classList.toggle("done", i < step);
      b.disabled = i > step && !(i === step + 1 && canNext());
    });
    q("#setup-back").style.visibility = step === 0 ? "hidden" : "";
    q("#setup-count").textContent = `${step + 1} из ${STEPS.length}`;
    nextBtn.innerHTML = step === STEPS.length - 1 ? `Познакомиться ${icon("i-heart")}` : step === 0 ? `Начать ${icon("i-arrow")}` : `Дальше ${icon("i-arrow")}`;
    nextBtn.disabled = !canNext();
  };

  const go = (to: number) => {
    cleanup?.();
    cleanup = null;
    step = Math.max(0, Math.min(STEPS.length - 1, to));
    void postJson("/api/setup", { step }).catch(() => {});
    content.replaceChildren(RENDER[step]());
    refreshNav();
  };

  /* ── шаг 0: привет ── */
  const stepHello = () => {
    const w = el("div", "setup-hello");
    w.innerHTML = `
      <div class="wiz-ava big">А</div>
      <h3>Привет, я Ая</h3>
      <p>Настроим меня за пару минут: где я буду думать, каким голосом говорить и как меня звать.
      Всё можно поменять потом в консоли.</p>`;
    return w;
  };

  /* ── шаг 1: модель для ответов ── */
  const stepModel = () => {
    const w = el("div", "setup-step");
    // облачная Ollama стала платной — у кого она стояла, предлагаем бесплатный OpenRouter
    const actual = llmKindOf(st.llm);
    let kind: LlmKind = actual === "ollama_cloud" ? "openrouter" : actual;
    const keySaved = (k: LlmKind) => st.llm.api_key_set && k === actual;
    const p = llmPreset(kind);
    const mine = kind === actual;
    const form = { type: p.type as string, base_url: (mine && st.llm.base_url) || p.base_url, model: (mine && st.llm.model) || p.model, api_key: "" };
    w.innerHTML = `
      <h3>Где Ая будет думать</h3>
      <p class="dim">По умолчанию — бесплатная модель gemma4 на OpenRouter. Нужен только ключ, карта не нужна.</p>
      <div class="card setup-card">
        <label class="setup-label" id="llm-key-label">Ключ OpenRouter</label>
        <input class="inp" id="llm-key" type="password" autocomplete="off">
        <div class="setup-status" id="llm-status"></div>
        <details class="setup-how" id="llm-how" open>
          <summary>Где взять ключ</summary>
          <ol id="llm-how-list"></ol>
          <button class="top-btn" id="llm-open">Открыть страницу ключей</button>
        </details>
      </div>
      <details class="setup-more" id="llm-more"${kind === "openrouter" ? "" : " open"}>
        <summary>Другая модель</summary>
        <div class="card setup-card">
          <div class="seg-row setup-seg" id="llm-kind">
            <button class="seg" data-k="openrouter">OpenRouter (бесплатно)</button>
            <button class="seg" data-k="ollama_cloud">Ollama в облаке (платно)</button>
            <button class="seg" data-k="ollama_local">Ollama на этом компьютере</button>
            <button class="seg" data-k="openai">Сервис, совместимый с OpenAI</button>
          </div>
          <label class="setup-label">Адрес</label>
          <input class="inp" id="llm-url">
          <label class="setup-label">Модель</label>
          <select class="inp sel" id="llm-model-sel"></select>
          <input class="inp" id="llm-model" placeholder="или впиши название">
          <button class="top-btn" id="llm-check">Проверить</button>
        </div>
      </details>
      <button class="link-btn" id="llm-skip">Пропустить пока</button>
      <div class="dim setup-warn" id="llm-skip-note" hidden>Без модели Ая не сможет отвечать. Ключ можно вставить потом: консоль → Мозг.</div>`;
    const key = w.querySelector<HTMLInputElement>("#llm-key")!;
    const status = w.querySelector<HTMLElement>("#llm-status")!;
    const url = w.querySelector<HTMLInputElement>("#llm-url")!;
    const msel = w.querySelector<HTMLSelectElement>("#llm-model-sel")!;
    const mtxt = w.querySelector<HTMLInputElement>("#llm-model")!;
    const fillModels = (models: string[]) => {
      const opts = modelOptions(models, form.model);
      msel.innerHTML = opts.map((m) => `<option value="${esc(m)}"${m === form.model ? " selected" : ""}>${esc(m)}</option>`).join("");
      msel.hidden = !opts.length;
    };
    const HOW: Partial<Record<LlmKind, { label: string; link: string; steps: string[] }>> = {
      openrouter: {
        label: "Ключ OpenRouter",
        link: "openrouter_keys",
        steps: [
          "Нажми «Открыть страницу ключей» и войди на openrouter.ai (можно через Google).",
          "Нажми <b>Create API Key</b>, придумай любое имя, остальное не трогай.",
          "Скопируй ключ (начинается с <b>sk-or-</b>) и вставь в поле выше — проверю сама.",
          "Бесплатно — 50 ответов в день. Хватит на обычный день разговоров.",
        ],
      },
      ollama_cloud: {
        label: "Ключ ollama.com",
        link: "ollama_keys",
        steps: [
          "Облачные модели Ollama платные: нужен тариф или деньги на счёте.",
          "Нажми «Открыть страницу ключей», затем <b>Add API Key</b>.",
          "Скопируй ключ и вставь в поле выше — проверю сама.",
        ],
      },
    };
    const markKind = () => {
      w.querySelectorAll<HTMLButtonElement>("#llm-kind button").forEach((b) => b.classList.toggle("active", b.dataset.k === kind));
      const how = HOW[kind];
      w.querySelector<HTMLElement>("#llm-key-label")!.textContent = how?.label ?? "Ключ";
      w.querySelector<HTMLElement>("#llm-how")!.hidden = !how;
      w.querySelector<HTMLElement>("#llm-how-list")!.innerHTML = (how?.steps ?? []).map((s) => `<li>${s}</li>`).join("");
      key.placeholder = !llmPreset(kind).needsKey ? "ключ не нужен" : keySaved(kind) ? "ключ сохранён — вставь новый, чтобы заменить" : "вставь ключ сюда";
    };
    url.value = form.base_url;
    mtxt.value = "";
    fillModels([]);
    markKind();
    if (llmOk) status.innerHTML = `<span class="ok">${icon("i-check")} Ая на связи</span>`;

    let seq = 0;
    const check = async () => {
      const my = ++seq;
      form.base_url = url.value.trim() || form.base_url;
      form.model = mtxt.value.trim() || msel.value || form.model;
      form.api_key = key.value.trim();
      if (llmPreset(kind).needsKey && !form.api_key && !keySaved(kind)) {
        status.textContent = "";
        return;
      }
      status.innerHTML = `<span class="dim">Проверяю…</span>`;
      const r = (await postJson("/api/setup/llm/check", form).catch(() => ({ ok: false, error: "offline", models: [] }))) as { ok: boolean; error: string; models: string[] };
      if (my !== seq) return;
      if (r.models?.length) fillModels(r.models);
      llmOk = r.ok;
      status.innerHTML = r.ok ? `<span class="ok">${icon("i-check")} Ая на связи</span>` : `<span class="bad">${esc(errorText(r.error))}</span>`;
      refreshNav();
    };
    let t = 0;
    const later = () => {
      window.clearTimeout(t);
      t = window.setTimeout(() => void check(), 400);
    };
    key.addEventListener("paste", later);
    key.addEventListener("input", later);
    w.querySelector("#llm-open")!.addEventListener("click", () => {
      const target = HOW[kind]?.link ?? "openrouter_keys";
      void postJson("/api/setup/open", { target }).catch(() => toast(target === "ollama_keys" ? "Открой ollama.com/settings/keys в браузере" : "Открой openrouter.ai/settings/keys в браузере"));
    });
    w.querySelector("#llm-check")!.addEventListener("click", () => void check());
    msel.addEventListener("change", () => {
      mtxt.value = "";
      later();
    });
    w.querySelectorAll<HTMLButtonElement>("#llm-kind button").forEach((b) =>
      b.addEventListener("click", () => {
        kind = b.dataset.k as LlmKind;
        const np = llmPreset(kind);
        Object.assign(form, { type: np.type, base_url: np.base_url, model: np.model });
        url.value = np.base_url;
        mtxt.value = "";
        fillModels([]);
        markKind();
        llmOk = false;
        refreshNav();
        if (!np.needsKey) void check();
      }),
    );
    w.querySelector("#llm-skip")!.addEventListener("click", () => {
      w.querySelector<HTMLElement>("#llm-skip-note")!.hidden = false;
      llmOk = true; // пропустить можно, но предупредили
      refreshNav();
    });
    return w;
  };

  /* ── шаг 2: голос ── */
  let audio: HTMLAudioElement | null = null;
  const say = (voice: string, btn?: HTMLElement) => {
    audio?.pause();
    btn?.classList.add("busy");
    audio = new Audio(`${CORE}/api/speak?text=${encodeURIComponent(GREETING)}&voice=${encodeURIComponent(voice)}`);
    audio.onplaying = audio.onended = audio.onerror = () => btn?.classList.remove("busy");
    void audio.play().catch(() => btn?.classList.remove("busy"));
  };
  const packCard = (name: string, packs: Packs) => {
    const p = packs.packs[name];
    const card = el("div", "card setup-card setup-pack");
    const draw = (s: PackState) => {
      const line = { none: `Скачать (${gb(s.size_mb)})`, running: "Качаю… можно идти дальше", done: "Готово", error: "Не получилось — попробовать ещё раз" }[s.state];
      card.innerHTML = `<div class="grow"><b>${esc(s.title)}</b><small class="dim block">${s.state === "error" ? esc(s.error.slice(0, 140)) : "по желанию, загрузка идёт в фоне"}</small></div>`;
      const b = el("button", "top-btn", line) as HTMLButtonElement;
      b.disabled = s.state === "running" || s.state === "done";
      b.addEventListener("click", async () => {
        b.disabled = true;
        draw((await postJson(`/api/setup/packs/${name}`, {}).catch(() => ({ ...s, state: "error", error: "ядро не ответило" }))) as unknown as PackState);
      });
      card.append(b);
    };
    draw(p);
    return { card, draw };
  };
  const watchPacks = (cards: Record<string, (s: PackState) => void>, onWhisper?: (w: Packs["whisper"]) => void) => {
    const id = window.setInterval(async () => {
      const r = await fetchJson<Packs>("/api/setup/packs").catch(() => null);
      if (!r) return;
      for (const [n, draw] of Object.entries(cards)) if (r.packs[n]?.state === "running" || r.packs[n]?.state === "done" || r.packs[n]?.state === "error") draw(r.packs[n]);
      onWhisper?.(r.whisper);
    }, 2000);
    cleanup = () => window.clearInterval(id);
  };

  const stepVoice = () => {
    const w = el("div", "setup-step");
    w.innerHTML = `
      <h3>Каким голосом говорить</h3>
      <p class="dim">Живой облачный голос Fish — бесплатный, нужен ключ. Без ключа Ая говорит простым голосом.</p>
      <div class="card setup-card">
        <label class="setup-label">Ключ fish.audio</label>
        <input class="inp" id="fish-key" type="password" autocomplete="off" placeholder="${st.fish_key_set ? "ключ сохранён — вставь новый, чтобы заменить" : "вставь ключ сюда"}">
        <div class="setup-status" id="fish-status">${st.fish_key_set ? `<span class="ok">${icon("i-check")} Ключ сохранён</span>` : ""}</div>
        <details class="setup-how">
          <summary>Где взять ключ</summary>
          <ol>
            <li>Нажми «Открыть страницу ключей» и войди на fish.audio.</li>
            <li>Нажми <b>Create</b> (новый ключ), скопируй его.</li>
            <li>Вставь в поле выше — я сразу скажу что-нибудь.</li>
          </ol>
          <button class="top-btn" id="fish-open">Открыть страницу ключей</button>
        </details>
      </div>
      <div class="setup-voices" id="voices"></div>
      <button class="link-btn" id="fish-skip">Пропустить — говорить простым голосом</button>
      <div id="packs"></div>`;
    const key = w.querySelector<HTMLInputElement>("#fish-key")!;
    const status = w.querySelector<HTMLElement>("#fish-status")!;
    const list = w.querySelector<HTMLElement>("#voices")!;
    let current = "fish_miyuki";
    void fetchJson<{ tts_voice_id?: string }>("/api/config").then((c) => {
      if (c.tts_voice_id?.startsWith("fish_")) current = c.tts_voice_id;
      draw();
    });
    const draw = () => {
      list.replaceChildren(
        ...FISH.map((v) => {
          const li = el("div", `set-item${v.id === current ? " chosen" : ""}`);
          li.innerHTML = `<span class="grow">${esc(v.label)}<small class="dim block">${esc(v.sub)}</small></span>`;
          const play = el("button", "round-btn sm") as HTMLButtonElement;
          play.innerHTML = icon("i-play");
          play.title = "Послушать";
          play.addEventListener("click", () => say(v.id, play));
          const pick = el("button", "top-btn sm", v.id === current ? "Выбран" : "Выбрать") as HTMLButtonElement;
          pick.addEventListener("click", async () => {
            current = v.id;
            await postJson("/api/config", { voice_id: v.id }).catch(() => {});
            draw();
          });
          li.append(play, pick);
          return li;
        }),
      );
    };
    let t = 0;
    const check = async () => {
      const k = key.value.trim();
      if (!k) return;
      status.innerHTML = `<span class="dim">Проверяю…</span>`;
      const r = (await postJson("/api/setup/fish/check", { key: k }).catch(() => ({ ok: false, error: "offline" }))) as { ok: boolean; error: string };
      status.innerHTML = r.ok ? `<span class="ok">${icon("i-check")} Голос работает</span>` : `<span class="bad">${esc(errorText(r.error))}</span>`;
      if (r.ok) {
        key.value = "";
        say(current);
      }
    };
    const later = () => {
      window.clearTimeout(t);
      t = window.setTimeout(() => void check(), 400);
    };
    key.addEventListener("paste", later);
    key.addEventListener("input", later);
    w.querySelector("#fish-open")!.addEventListener("click", () => void postJson("/api/setup/open", { target: "fish_keys" }).catch(() => toast("Открой fish.audio/app/api-keys в браузере")));
    w.querySelector("#fish-skip")!.addEventListener("click", () => {
      say(current); // без ключа ядро говорит голосом Edge
      go(step + 1);
    });
    void fetchJson<Packs>("/api/setup/packs").then((packs) => {
      const lv = packCard("local_voices", packs);
      w.querySelector("#packs")!.append(lv.card, el("small", "dim block setup-lic", "Голос F5 — лицензия CC BY-NC 4.0: только некоммерческое использование. Веса качаются с Hugging Face."));
      watchPacks({ local_voices: lv.draw });
    });
    return w;
  };

  /* ── шаг 3: микрофон ── */
  const stepMic = () => {
    const w = el("div", "setup-step");
    w.innerHTML = `
      <h3>Как меня звать</h3>
      <div class="card setup-card">
        <label class="setup-label">Микрофон</label>
        <select class="inp sel" id="mic-sel"><option value="">По умолчанию</option></select>
        <div class="setup-meter"><i id="mic-level"></i></div>
        <small class="dim">Скажи что-нибудь — полоска должна двигаться.</small>
      </div>
      <div class="card setup-card">
        <div class="seg-row setup-seg" id="call-mode">
          <button class="seg" data-m="key">Клавишей</button>
          <button class="seg" data-m="wake">Голосом: «Ая, …»</button>
        </div>
        <div id="hk"></div>
        <small class="dim block" id="whisper-line">Распознавание речи: готовлю…</small>
      </div>
      <div id="gpu"></div>`;
    w.querySelector("#hk")!.append(hotkeyField("hotkey_talk", "Клавиша, чтобы говорить"));
    const sel = w.querySelector<HTMLSelectElement>("#mic-sel")!;
    const level = w.querySelector<HTMLElement>("#mic-level")!;
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    let raf = 0;
    const stopMic = () => {
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close().catch(() => {});
      stream = null;
      ctx = null;
    };
    const startMic = async (deviceId: string) => {
      stopMic();
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: deviceId ? { deviceId: { exact: deviceId } } : true });
      } catch {
        level.parentElement!.replaceWith(el("div", "bad", "Нет доступа к микрофону — разреши его в настройках Windows"));
        return;
      }
      ctx = new AudioContext();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      const tick = () => {
        an.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        level.style.width = `${Math.min(100, (peak / 64) * 100)}%`;
        raf = requestAnimationFrame(tick);
      };
      tick();
      const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput" && d.deviceId !== "default");
      const saved = localStorage.getItem("aya.micId") || "";
      sel.innerHTML = `<option value="">По умолчанию</option>` + devs.map((d) => `<option value="${esc(d.deviceId)}"${d.deviceId === saved ? " selected" : ""}>${esc(d.label || "Микрофон")}</option>`).join("");
    };
    sel.addEventListener("change", () => {
      try {
        localStorage.setItem("aya.micId", sel.value);
      } catch {}
      void startMic(sel.value);
    });
    void startMic(localStorage.getItem("aya.micId") || "");

    const modeBtns = w.querySelectorAll<HTMLButtonElement>("#call-mode button");
    const markMode = (m: string) => modeBtns.forEach((b) => b.classList.toggle("active", b.dataset.m === m));
    void fetchJson<{ wake_word?: boolean }>("/api/config").then((c) => markMode(c.wake_word ? "wake" : "key"));
    modeBtns.forEach((b) =>
      b.addEventListener("click", async () => {
        markMode(b.dataset.m!);
        // клавиша работает в обоих режимах; «Ая, …» — добавляется к ней
        await postJson("/api/config", { voice_mode: "hotkey", wake_word: b.dataset.m === "wake" }).catch(() => {});
      }),
    );
    void postJson("/api/config", { voice_mode: "hotkey" }).catch(() => {});

    const wl = w.querySelector<HTMLElement>("#whisper-line")!;
    const showWhisper = (s: Packs["whisper"]) => {
      wl.textContent = { done: "Распознавание речи готово", running: "Распознавание речи качается (~0,5 ГБ)…", error: "Распознавание докачается при первой фразе", none: "Распознавание речи: готовлю…" }[s.state as "done"] ?? wl.textContent;
    };
    void fetchJson<Packs>("/api/setup/packs").then((packs) => {
      showWhisper(packs.whisper);
      const cards: Record<string, (s: PackState) => void> = {};
      if (packs.gpu) {
        const g = packCard("gpu", packs);
        w.querySelector("#gpu")!.append(g.card);
        cards.gpu = g.draw;
      }
      watchPacks(cards, showWhisper);
      const prev = cleanup;
      cleanup = () => {
        prev?.();
        stopMic();
      };
    });
    cleanup = stopMic;
    return w;
  };

  /* ── шаг 4: кто она ── */
  const stepWho = () => {
    const w = el("div", "setup-step");
    w.innerHTML = `
      <h3>Кто она</h3>
      <div class="card setup-card">
        <label class="setup-label">Имя</label>
        <input class="inp" id="who-name" maxlength="40">
        <label class="setup-label">Внешность</label>
        <div class="setup-models" id="who-models"></div>
      </div>
      <button class="link-btn" id="who-more">Характер и голос подробнее — «Собрать Аю»</button>`;
    const name = w.querySelector<HTMLInputElement>("#who-name")!;
    let profile: Record<string, unknown> | null = null;
    void fetchJson<{ profile: Record<string, unknown> }>("/api/persona").then((v) => {
      profile = v.profile;
      name.value = String(v.profile.name || "Ая");
    });
    let t = 0;
    name.addEventListener("input", () => {
      window.clearTimeout(t);
      t = window.setTimeout(() => {
        if (!profile) return;
        profile.name = name.value.trim() || "Ая";
        void postJson("/api/persona", { profile }).catch(() => {});
      }, 500);
    });
    const box2 = w.querySelector<HTMLElement>("#who-models")!;
    void fetchJson<Record<string, ModelInfo>>("/api/models").then((models) => {
      const names = Object.keys(models);
      if (!names.length) {
        box2.append(el("div", "dim", "Haru — встроенная. Свои модели можно добавить потом в консоли → Внешность."));
        return;
      }
      for (const n of names) {
        const b = el("button", `top-btn${models[n].active ? " active" : ""}`, n) as HTMLButtonElement;
        b.addEventListener("click", async () => {
          await postJson("/api/models", { name: n }).catch(() => {});
          box2.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
        });
        box2.append(b);
      }
    });
    w.querySelector("#who-more")!.addEventListener("click", () => {
      location.hash = "wizard";
    });
    return w;
  };

  /* ── шаг 5: готово ── */
  const stepDone = () => {
    const w = el("div", "setup-step setup-hello");
    w.innerHTML = `
      <div class="wiz-ava big">${icon("i-heart")}</div>
      <h3>Всё готово</h3>
      <label class="set-item setup-auto"><input type="checkbox" id="auto" checked> <span class="grow">Запускать вместе с Windows</span></label>
      <p class="dim">Ещё возможности — в консоли: Telegram, музыка, навыки, свои голоса.</p>`;
    return w;
  };

  const RENDER = [stepHello, stepModel, stepVoice, stepMic, stepWho, stepDone];

  const finish = async () => {
    nextBtn.disabled = true;
    const auto = box.querySelector<HTMLInputElement>("#auto")?.checked ?? true;
    await inApp(async () => {
      const a = await import("@tauri-apps/plugin-autostart");
      if (auto) await a.enable();
      else await a.disable();
    });
    await postJson("/api/setup", { done: true, step: 0 }).catch(() => {}); // «Пройти заново» начнётся с начала
    cleanup?.();
    back.classList.remove("show");
    window.setTimeout(() => back.remove(), 220);
    open = false;
    await inApp(async () => {
      const { emitTo } = await import("@tauri-apps/api/event");
      await emitTo("main", "aya-setup-done", {});
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      await getCurrentWindow().hide();
    });
    toast("Готово! Ая уже на столе");
  };

  box.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.step))));
  q("#setup-back").addEventListener("click", () => go(step - 1));
  nextBtn.addEventListener("click", () => (step < STEPS.length - 1 ? go(step + 1) : void finish()));
  go(step);
}

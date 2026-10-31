/* Мастер «Собери свою Аю»: пять шагов, черновик применяется только по «Готово». */
import { allVoices, voiceMaker, type Voice } from "./voice-extra";
import {
  LENGTHS,
  MODULES,
  TAG_IDEAS,
  TRAITS,
  addTag,
  applyPreset,
  clone,
  traitWord,
  type PersonaView,
  type Profile,
} from "./persona-store";
import { showPreview, wavePreview } from "./preview";
import { CORE, el, esc, fetchJson, icon, postJson, toast } from "./ui";

type ModelInfo = { active: boolean; file: string };

const STEPS = ["Кто она", "Внешность", "Голос", "Характер", "Возможности"];

interface Draft {
  profile: Profile;
  voice: string;
  model: string;
  modelFile: string;
}

let open = false;

export async function openWizard(onDone: () => void): Promise<void> {
  if (open) return;
  open = true;
  let view: PersonaView;
  let cfg: { tts_voice_id?: string } = {};
  let models: Record<string, ModelInfo> = {};
  try {
    [view, cfg, models] = await Promise.all([
      fetchJson<PersonaView>("/api/persona"),
      fetchJson<{ tts_voice_id?: string }>("/api/config"),
      fetchJson<Record<string, ModelInfo>>("/api/models").catch(() => ({})),
    ]);
  } catch {
    open = false;
    toast("Ядро не отвечает — запусти core и попробуй снова");
    return;
  }
  const activeModel = Object.entries(models).find(([, m]) => m.active);
  const draft: Draft = {
    profile: clone(view.profile),
    voice: cfg.tts_voice_id || "fish_miyuki",
    model: activeModel?.[0] || "",
    modelFile: activeModel?.[1].file || "haru/Haru.model3.json",
  };
  const initial = JSON.stringify({ p: draft.profile, v: draft.voice, m: draft.model });
  let step = 0;
  let presetId = "";
  let voices: Voice[] = await allVoices();

  const back = el("div", "wiz-backdrop");
  const box = el("div", "wiz");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.innerHTML = `
    <header class="wiz-head">
      <div>
        <div class="kicker">конструктор персоны</div>
        <h2 class="wiz-title">Собери свою Аю</h2>
      </div>
      <button class="icon-btn" data-x aria-label="Закрыть">${icon("i-x")}</button>
    </header>
    <div class="wiz-body">
      <nav class="wiz-steps">${STEPS.map((s, i) => `<button data-step="${i}"><i>${i + 1}</i>${s}</button>`).join("")}</nav>
      <div class="wiz-main">
        <div class="wiz-strip">
          <div class="wiz-ava" id="wiz-ava"></div>
          <div class="wiz-strip-txt">
            <div class="kicker">предпросмотр</div>
            <b id="wiz-name"></b>
            <span id="wiz-greet"></span>
          </div>
          <button class="round-btn" id="wiz-play" title="Послушать приветствие">${icon("i-play")}</button>
        </div>
        <div class="wiz-content" id="wiz-content"></div>
      </div>
    </div>
    <footer class="wiz-foot">
      <button class="btn ghost" id="wiz-back">Назад</button>
      <span class="wiz-count" id="wiz-count"></span>
      <button class="btn grad" id="wiz-next">Далее ${icon("i-arrow")}</button>
    </footer>`;
  back.append(box);
  document.body.append(back);
  requestAnimationFrame(() => back.classList.add("show"));

  const content = box.querySelector<HTMLElement>("#wiz-content")!;
  const q = <T extends HTMLElement>(sel: string) => box.querySelector<T>(sel)!;

  const close = (force = false) => {
    const dirty = JSON.stringify({ p: draft.profile, v: draft.voice, m: draft.model }) !== initial;
    if (!force && dirty && !confirm("Выйти без сохранения? Изменения пропадут.")) return;
    back.classList.remove("show");
    window.setTimeout(() => back.remove(), 220);
    document.removeEventListener("keydown", onKey);
    open = false;
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);
  q("[data-x]").addEventListener("click", () => close());
  back.addEventListener("click", (e) => {
    if (e.target === back) close();
  });

  const strip = () => {
    q("#wiz-name").textContent = draft.profile.name || "Без имени";
    q("#wiz-greet").textContent = `«${draft.profile.greeting || "Привет! Я рядом."}»`;
    q("#wiz-ava").textContent = (draft.profile.name || "А").slice(0, 1).toUpperCase();
  };

  let audio: HTMLAudioElement | null = null;
  const playSample = async (voice: string, btn: HTMLElement) => {
    audio?.pause();
    if (voice === "none") return;
    btn.classList.add("busy");
    const text = draft.profile.greeting || `Привет! Я ${draft.profile.name || "Ая"}, я рядом.`;
    // характер с ползунков — облачный голос звучит им ещё до сохранения
    const traits = encodeURIComponent(JSON.stringify(draft.profile.traits));
    audio = new Audio(`${CORE}/api/speak?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(voice)}&traits=${traits}`);
    audio.onplaying = () => btn.classList.remove("busy");
    audio.onended = audio.onerror = () => btn.classList.remove("busy");
    try {
      await audio.play();
    } catch {
      btn.classList.remove("busy");
      toast("Голос не ответил — движок, возможно, ещё грузится");
    }
  };
  q("#wiz-play").addEventListener("click", (e) => void playSample(draft.profile.modules.voice ? draft.voice : "none", e.currentTarget as HTMLElement));

  /* ── шаги ── */

  const stepWho = () => {
    const p = draft.profile;
    content.innerHTML = `
      <div class="field-grid">
        <label class="field"><span>Как её зовут</span><input class="inp" id="f-name" maxlength="24" value="${esc(p.name)}" placeholder="Ая" /></label>
        <label class="field"><span>Как зовут тебя</span><input class="inp" id="f-user" maxlength="32" value="${esc(p.user_name)}" placeholder="необязательно" /></label>
      </div>
      <label class="field"><span>Приветствие</span><input class="inp" id="f-greet" maxlength="160" value="${esc(p.greeting)}" placeholder="Привет! Я рядом." /></label>
      <div class="field"><span>Обращение</span>
        <div class="seg-row">${(["ты", "вы"] as const).map((a) => `<button class="seg${p.address === a ? " active" : ""}" data-addr="${a}">на «${a}»</button>`).join("")}</div>
      </div>
      <div class="field"><span>Готовые образы <small>— начни с близкого, потом подкрутишь</small></span>
        <div class="preset-grid">${view.presets
          .map((pr) => `<button class="preset${presetId === pr.id ? " active" : ""}" data-preset="${pr.id}"><b>${esc(pr.title)}</b><small>${esc(pr.sub)}</small></button>`)
          .join("")}</div>
      </div>`;
    const bind = (id: string, key: "name" | "user_name" | "greeting") =>
      content.querySelector<HTMLInputElement>(id)!.addEventListener("input", (e) => {
        draft.profile[key] = (e.target as HTMLInputElement).value;
        strip();
      });
    bind("#f-name", "name");
    bind("#f-user", "user_name");
    bind("#f-greet", "greeting");
    content.querySelectorAll<HTMLButtonElement>("[data-addr]").forEach((b) =>
      b.addEventListener("click", () => {
        draft.profile.address = b.dataset.addr as "ты" | "вы";
        stepWho();
      }),
    );
    content.querySelectorAll<HTMLButtonElement>("[data-preset]").forEach((b) =>
      b.addEventListener("click", () => {
        const pr = view.presets.find((x) => x.id === b.dataset.preset)!;
        draft.profile = applyPreset(draft.profile, pr);
        presetId = pr.id;
        toast(`Образ «${pr.title}» — характер можно подправить на 4-м шаге`);
        stepWho();
      }),
    );
  };

  const stepLook = () => {
    const entries = Object.entries(models);
    content.innerHTML = `
      <div class="look-grid">
        <div class="look-list">${
          entries.length
            ? entries
                .map(
                  ([name, m]) =>
                    `<button class="opt${draft.model === name ? " active" : ""}" data-model="${esc(name)}" data-file="${esc(m.file)}"><span class="opt-label">${esc(name)}</span><span class="opt-sub">${m.active ? "сейчас на экране" : "Live2D"}</span><span class="opt-check">${draft.model === name ? "✓" : ""}</span></button>`,
                )
                .join("")
            : `<div class="hint-box">Моделей нет. Положи папку Live2D-модели (с <code>*.model3.json</code>) в <code>~/.aya/models/&lt;имя&gt;</code> — она появится здесь.</div>`
        }</div>
        <div class="look-stage" id="look-stage"></div>
      </div>
      <p class="hint">Совет: в окне Аи размер меняется колесом мыши с <kbd>Ctrl</kbd> или в меню по правому клику.</p>`;
    const stage = content.querySelector<HTMLElement>("#look-stage")!;
    void showPreview(stage, draft.modelFile);
    content.querySelectorAll<HTMLButtonElement>("[data-model]").forEach((b) =>
      b.addEventListener("click", () => {
        draft.model = b.dataset.model!;
        draft.modelFile = b.dataset.file!;
        stepLook();
        window.setTimeout(wavePreview, 900);
      }),
    );
  };

  const stepVoice = () => {
    const off = !draft.profile.modules.voice;
    content.innerHTML = `
      <ul class="voice-choice">${voices.map(
        (v) => `<li class="opt${!off && v.id === draft.voice ? " active" : ""}" data-voice="${v.id}">
            <span class="opt-ava">${esc(v.label.slice(0, 1))}</span>
            <span class="opt-txt"><span class="opt-label">${esc(v.label)}</span><span class="opt-sub">${esc(v.sub)}</span></span>
            <button class="round-btn sm" data-play="${v.id}" title="Послушать">${icon("i-play")}</button>
          </li>`,
      ).join("")}
        <li class="opt${off ? " active" : ""}" data-voice="none">
          <span class="opt-ava">—</span>
          <span class="opt-txt"><span class="opt-label">Без голоса</span><span class="opt-sub">только текст и пузырь над головой</span></span>
        </li>
      </ul>
      <details class="maker-wrap"><summary>+ Свой голос — записать или загрузить образец</summary><div id="maker-slot"></div></details>
      <p class="hint">Локальные голоса (F5, Qwen) при первом прослушивании грузятся в видеопамять — пара секунд.</p>`;
    content.querySelector("#maker-slot")!.append(
      voiceMaker(async (id) => {
        voices = await allVoices();
        draft.profile.modules.voice = true;
        draft.voice = id;
        stepVoice();
      }),
    );
    content.querySelectorAll<HTMLElement>("[data-voice]").forEach((li) =>
      li.addEventListener("click", (e) => {
        if ((e.target as HTMLElement).closest("[data-play]")) return;
        const v = li.dataset.voice!;
        if (v === "none") draft.profile.modules.voice = false;
        else {
          draft.profile.modules.voice = true;
          draft.voice = v;
        }
        stepVoice();
      }),
    );
    content.querySelectorAll<HTMLButtonElement>("[data-play]").forEach((b) =>
      b.addEventListener("click", () => void playSample(b.dataset.play!, b)),
    );
  };

  let previewTimer = 0;
  const stepChar = () => {
    const p = draft.profile;
    content.innerHTML = `
      <div class="field"><span>Роль</span>
        <div class="role-grid">${view.roles
          .map((r) => `<button class="role${p.role === r.id ? " active" : ""}" data-role="${r.id}" title="${esc(r.sub)}">${esc(r.title)}</button>`)
          .join("")}</div>
        ${p.role === "custom" ? `<textarea class="inp" id="f-role" rows="2" maxlength="300" placeholder="Например: соседка по общежитию, учит меня японскому">${esc(p.role_text)}</textarea>` : ""}
      </div>
      <div class="traits">${TRAITS.map(
        (t) => `<label class="trait">
          <span class="trait-head"><b>${t.title}</b><em data-word="${t.id}">${traitWord(p.traits[t.id], t)}</em></span>
          <input type="range" min="0" max="100" step="1" value="${p.traits[t.id]}" data-trait="${t.id}" />
          <span class="trait-ends"><small>${t.lo}</small><small>${t.hi}</small></span>
        </label>`,
      ).join("")}</div>
      <div class="field-grid">
        <div class="field"><span>Длина ответов</span>
          <div class="seg-row">${LENGTHS.map((l) => `<button class="seg${p.length === l.id ? " active" : ""}" data-len="${l.id}">${l.title}</button>`).join("")}</div>
        </div>
        <label class="field row"><span>Эмодзи в ответах</span><label class="switch"><input type="checkbox" id="f-emoji" ${p.emoji ? "checked" : ""}/><span class="slider"></span></label></label>
      </div>
      <div class="field"><span>Особенности <small>— Enter, чтобы добавить</small></span>
        <div class="tags" id="tags">${p.tags.map((t, i) => `<span class="tag">${esc(t)}<button data-untag="${i}" aria-label="убрать">×</button></span>`).join("")}<input id="f-tag" maxlength="40" placeholder="добавить…" /></div>
        <div class="tag-ideas">${TAG_IDEAS.filter((t) => !p.tags.includes(t))
          .slice(0, 7)
          .map((t) => `<button data-idea="${esc(t)}">+ ${esc(t)}</button>`)
          .join("")}</div>
      </div>
      <label class="field"><span>Интересы</span><input class="inp" id="f-int" maxlength="300" value="${esc(p.interests)}" placeholder="аниме, программирование, чай…" /></label>
      <details class="prompt-peek"><summary>Как она это поймёт (системный промпт)</summary><pre id="prompt-pre">…</pre></details>`;
    const refreshPrompt = () => {
      window.clearTimeout(previewTimer);
      previewTimer = window.setTimeout(async () => {
        const pre = content.querySelector("#prompt-pre");
        if (!pre) return;
        const r = await postJson("/api/persona/preview", { profile: draft.profile });
        pre.textContent = String(r.prompt || "");
      }, 300);
    };
    refreshPrompt();
    content.querySelectorAll<HTMLButtonElement>("[data-role]").forEach((b) =>
      b.addEventListener("click", () => {
        draft.profile.role = b.dataset.role!;
        stepChar();
      }),
    );
    content.querySelector<HTMLTextAreaElement>("#f-role")?.addEventListener("input", (e) => {
      draft.profile.role_text = (e.target as HTMLTextAreaElement).value;
      refreshPrompt();
    });
    content.querySelectorAll<HTMLInputElement>("[data-trait]").forEach((r) =>
      r.addEventListener("input", () => {
        const id = r.dataset.trait as keyof Profile["traits"];
        draft.profile.traits[id] = Number(r.value);
        const t = TRAITS.find((x) => x.id === id)!;
        content.querySelector(`[data-word="${id}"]`)!.textContent = traitWord(Number(r.value), t);
        refreshPrompt();
      }),
    );
    content.querySelectorAll<HTMLButtonElement>("[data-len]").forEach((b) =>
      b.addEventListener("click", () => {
        draft.profile.length = b.dataset.len as Profile["length"];
        stepChar();
      }),
    );
    content.querySelector<HTMLInputElement>("#f-emoji")!.addEventListener("change", (e) => {
      draft.profile.emoji = (e.target as HTMLInputElement).checked;
      refreshPrompt();
    });
    const tagIn = content.querySelector<HTMLInputElement>("#f-tag")!;
    tagIn.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        draft.profile.tags = addTag(draft.profile.tags, tagIn.value);
        stepChar();
        content.querySelector<HTMLInputElement>("#f-tag")?.focus();
      }
    });
    content.querySelectorAll<HTMLButtonElement>("[data-untag]").forEach((b) =>
      b.addEventListener("click", () => {
        draft.profile.tags.splice(Number(b.dataset.untag), 1);
        stepChar();
      }),
    );
    content.querySelectorAll<HTMLButtonElement>("[data-idea]").forEach((b) =>
      b.addEventListener("click", () => {
        draft.profile.tags = addTag(draft.profile.tags, b.dataset.idea!);
        stepChar();
      }),
    );
    content.querySelector<HTMLInputElement>("#f-int")!.addEventListener("input", (e) => {
      draft.profile.interests = (e.target as HTMLInputElement).value;
      refreshPrompt();
    });
  };

  const stepAbilities = () => {
    const m = draft.profile.modules;
    content.innerHTML = `
      <div class="module-grid">${MODULES.map(
        (mod) => `<label class="module${m[mod.id] ? " on" : ""}">
          <span class="module-ic">${icon(mod.icon)}</span>
          <span class="module-txt"><b>${mod.title}</b><small>${mod.sub}</small></span>
          <span class="switch"><input type="checkbox" data-mod="${mod.id}" ${m[mod.id] ? "checked" : ""}/><span class="slider"></span></span>
        </label>`,
      ).join("")}</div>
      <p class="hint">«Действия на ПК» включают безопасный набор. Команды PowerShell, SSH и запись файлов включаются отдельно в разделе «Навыки» и всегда спрашивают подтверждение.</p>
      <div class="summary-card">
        <div class="kicker">итог</div>
        <p><b>${esc(draft.profile.name || "Ая")}</b> — ${esc(view.roles.find((r) => r.id === draft.profile.role)?.title.toLowerCase() || "компаньонка")}, говорит на «${draft.profile.address}»,
        ${draft.profile.modules.voice ? `голос «${esc(voices.find((v) => v.id === draft.voice)?.label || draft.voice)}»` : "без голоса"},
        модель ${esc(draft.model || "по умолчанию")}.</p>
      </div>`;
    content.querySelectorAll<HTMLInputElement>("[data-mod]").forEach((c) =>
      c.addEventListener("change", () => {
        draft.profile.modules[c.dataset.mod as keyof Profile["modules"]] = c.checked;
        stepAbilities();
      }),
    );
  };

  const renderers = [stepWho, stepLook, stepVoice, stepChar, stepAbilities];

  const go = (n: number) => {
    step = Math.max(0, Math.min(STEPS.length - 1, n));
    box.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b, i) => {
      b.classList.toggle("active", i === step);
      b.classList.toggle("done", i < step);
    });
    q("#wiz-count").textContent = `Шаг ${step + 1} из ${STEPS.length}`;
    q<HTMLButtonElement>("#wiz-back").style.visibility = step === 0 ? "hidden" : "visible";
    q("#wiz-next").innerHTML = step === STEPS.length - 1 ? `Готово ${icon("i-check")}` : `Далее ${icon("i-arrow")}`;
    content.classList.remove("in");
    void content.offsetWidth;
    content.classList.add("in");
    renderers[step]();
    strip();
    box.querySelector(".wiz-main")!.scrollTop = 0;
    const nav = box.querySelector<HTMLElement>(".wiz-steps")!;
    const cur = box.querySelector<HTMLElement>(`[data-step="${step}"]`);
    if (cur && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = Math.max(0, cur.offsetLeft - 24);
  };

  box.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.step))));
  q("#wiz-back").addEventListener("click", () => go(step - 1));
  q("#wiz-next").addEventListener("click", async () => {
    if (step < STEPS.length - 1) return go(step + 1);
    const btn = q<HTMLButtonElement>("#wiz-next");
    btn.disabled = true;
    try {
      if (!draft.profile.name.trim()) draft.profile.name = "Ая";
      const res = await postJson("/api/persona", { profile: draft.profile });
      if (!res.ok) throw new Error("persona");
      if (draft.profile.modules.voice) await postJson("/api/config", { voice_id: draft.voice });
      if (draft.model && !models[draft.model]?.active) {
        await postJson("/api/models", { name: draft.model });
        await notifyMainReload();
      }
      toast(`Готово! ${draft.profile.name} собрана`);
      close(true);
      onDone();
    } catch {
      toast("Не удалось сохранить — ядро не отвечает");
    } finally {
      btn.disabled = false;
    }
  });

  go(0);
}

/** Сменили модель — главное окно перезагружает аватар. */
export async function notifyMainReload() {
  try {
    const { emitTo } = await import("@tauri-apps/api/event");
    await emitTo("main", "aya-reload", "model");
  } catch {
    /* консоль открыта в браузере — главное окно подхватит модель при перезапуске */
  }
}

/* Голос: распознавание, «Ая, …», свои голоса (запись/загрузка образца). */
import { TTS_VOICES } from "./cards";
import { CORE, el, esc, fetchJson, icon, postJson, switchEl, toast } from "./ui";

export type Voice = { id: string; label: string; sub: string; custom?: boolean };

/** Встроенные голоса + свои из ~/.aya/voices. */
export async function allVoices(): Promise<Voice[]> {
  try {
    const r = await fetchJson<{ custom: Voice[] }>("/api/voices");
    return [...(r.custom || []), ...TTS_VOICES];
  } catch {
    return [...TTS_VOICES];
  }
}

/* ── распознавание речи ── */
export function sttCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Распознавание речи"));
  const hint = el("div", "hint-box", "Whisper на видеокарте в ~3 раза быстрее процессора. «Авто» — GPU, если есть, иначе CPU.");
  const grid = el("div", "form-grid");
  const model = document.createElement("select");
  model.className = "inp sel";
  const labels: Record<string, string> = {
    base: "base — самый быстрый",
    small: "small — баланс (по умолчанию)",
    medium: "medium — точнее, медленнее",
    "large-v3-turbo": "large-v3-turbo — точнее всех (скачает ~1.6 ГБ)",
  };
  const device = document.createElement("select");
  device.className = "inp sel";
  for (const [v, t] of [["auto", "авто (GPU → CPU)"], ["cuda", "только видеокарта"], ["cpu", "только процессор"]]) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t;
    device.append(o);
  }
  const status = el("div", "hint-box");
  grid.append(model, device);
  card.append(hint, grid, status);
  type St = { model: string; device: string; models: string[]; loaded: boolean; active_device: string; error: string };
  const show = (s: St) => {
    status.textContent = s.loaded
      ? `сейчас работает на ${s.active_device === "cuda" ? "видеокарте" : "процессоре"}${s.error ? ` · ${s.error}` : ""}`
      : "модель загрузится при первой фразе";
  };
  const save = async () => {
    const s = (await postJson("/api/stt", { model: model.value, device: device.value })) as unknown as St;
    show(s);
    toast("Распознавание перенастроено — применится со следующей фразы");
  };
  model.addEventListener("change", () => void save());
  device.addEventListener("change", () => void save());
  void (async () => {
    try {
      const s = await fetchJson<St>("/api/stt");
      for (const m of s.models) {
        const o = document.createElement("option");
        o.value = m;
        o.textContent = labels[m] || m;
        model.append(o);
      }
      model.value = s.model;
      device.value = s.device;
      show(s);
    } catch {
      status.textContent = "ядро не отвечает";
    }
  })();
  return card;
}

/* ── «Ая, …» и быстрый старт голоса ── */
export function handsFreeCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Без кнопок"));
  const list = el("div", "set-list");
  const names = el("input", "inp") as HTMLInputElement;
  names.placeholder = "свои варианты имени через запятую: Аюшка, Аечка";
  const row = (title: string, sub: string, key: string) => {
    const li = el("div", "set-item");
    const t = el("span", "grow");
    t.innerHTML = `${esc(title)}<small class="dim block">${esc(sub)}</small>`;
    const sw = switchEl(false, async (v) => {
      await postJson("/api/config", { [key]: v });
    });
    li.append(t, sw);
    list.append(li);
    return sw.querySelector("input") as HTMLInputElement;
  };
  const wake = row("Откликаться на имя", "скажи «Ая, открой калькулятор» — кнопка не нужна; просто «Ая» — и она переспросит", "wake_word");
  const fast = row("Быстрый старт голоса", "первая фраза синтезируется вдвое быстрее (чуть проще звучит), остальные — в полном качестве", "tts_fast_first");
  let t = 0;
  names.addEventListener("input", () => {
    window.clearTimeout(t);
    t = window.setTimeout(() => void postJson("/api/config", { wake_names: names.value }), 500);
  });
  card.append(
    list,
    names,
    el(
      "div",
      "hint-box",
      "Микрофон слушает постоянно, но всё распознаётся на твоём ПК, в облако ничего не уходит. Фразы без имени тут же забываются. В режиме микрофона «По голосу» имя не нужно вовсе.",
    ),
  );
  void (async () => {
    try {
      const c = await fetchJson<{ wake_word?: boolean; wake_names?: string; tts_fast_first?: boolean }>("/api/config");
      wake.checked = !!c.wake_word;
      fast.checked = c.tts_fast_first !== false;
      names.value = c.wake_names || "";
    } catch {}
  })();
  return card;
}

/* ── облачный голос Fish: режим задержки и ключ ── */
const FISH_LATENCY: [string, string, string][] = [
  ["low", "Быстро", "первый звук ~0,6 с, изредка проще интонация"],
  ["balanced", "Баланс", "~0,8 с — почти так же быстро, стабильнее (по умолчанию)"],
  ["normal", "Качество", "~2,4 с — самое ровное звучание"],
];

export function fishCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Облачный голос Fish"));
  const segs = el("div", "seg-row");
  const sub = el("div", "hint-box");
  const buttons = FISH_LATENCY.map(([v, label, hint]) => {
    const b = el("button", "seg", label) as HTMLButtonElement;
    b.type = "button";
    b.dataset.v = v;
    b.title = hint;
    b.addEventListener("click", async () => {
      await postJson("/api/config", { fish_latency: v });
      mark(v);
      toast(`Fish: ${label.toLowerCase()} — со следующей фразы`);
    });
    segs.append(b);
    return b;
  });
  const mark = (v: string) => {
    for (const b of buttons) b.classList.toggle("active", b.dataset.v === v);
    sub.textContent = FISH_LATENCY.find((x) => x[0] === v)?.[2] ?? "";
  };
  const key = el("input", "inp") as HTMLInputElement;
  key.type = "password";
  key.autocomplete = "off";
  key.placeholder = "ключ API с fish.audio";
  const keyRow = el("div", "seg-row");
  const saveKey = el("button", "top-btn sm", "Сохранить ключ") as HTMLButtonElement;
  saveKey.addEventListener("click", async () => {
    await postJson("/api/config", { fish_api_key: key.value });
    key.value = "";
    key.placeholder = "ключ сохранён — введи новый, чтобы заменить";
    toast("Ключ Fish сохранён");
  });
  keyRow.append(key, saveKey);
  card.append(
    el("div", "dim", "Скорость первого звука для голосов с пометкой cloud. Звук играет с первых байт, пока Fish ещё синтезирует."),
    segs,
    sub,
    keyRow,
    el("div", "hint-box", "Если Fish не ответит за 8 с, фраза прозвучит запасным голосом (edge-tts)."),
  );
  void (async () => {
    try {
      const c = await fetchJson<{ fish_latency?: string; fish_key_set?: boolean }>("/api/config");
      mark(c.fish_latency || "balanced");
      if (c.fish_key_set) key.placeholder = "ключ сохранён — введи новый, чтобы заменить";
    } catch {
      sub.textContent = "ядро не отвечает";
    }
  })();
  return card;
}

/* ── музыка: YouTube, Яндекс Музыка, приглушение ── */
type MusicCfg = {
  service: string;
  duck: string;
  youtube_site: string;
  youtube_radio: boolean;
  yandex_target: string;
  yandex_target_set: string;
  ym_token_set: boolean;
  now?: { status: string; app: string; title: string; artist: string };
};

function segRow(options: [string, string, string][], onPick: (v: string) => Promise<void>) {
  const row = el("div", "seg-row");
  const hint = el("div", "dim");
  const btns = options.map(([v, label, sub]) => {
    const b = el("button", "seg", label) as HTMLButtonElement;
    b.type = "button";
    b.dataset.v = v;
    b.title = sub;
    b.addEventListener("click", async () => {
      await onPick(v);
      mark(v);
    });
    row.append(b);
    return b;
  });
  const mark = (v: string) => {
    for (const b of btns) b.classList.toggle("active", b.dataset.v === v);
    hint.textContent = options.find((o) => o[0] === v)?.[2] ?? "";
  };
  return { row, hint, mark };
}

export function musicCard(): HTMLElement {
  const card = el("div", "card");
  card.append(el("h3", "sec-title", "Музыка"));
  const save = (body: Partial<MusicCfg> & { ym_token?: string }) => postJson("/api/music/config", body);
  const now = el("div", "hint-box", "…");

  const service = segRow(
    [
      ["youtube", "YouTube", "«Ая, включи …» — видео откроется в браузере, после песни пойдут похожие"],
      ["yandex", "Яндекс Музыка", "с токеном Ая играет сама; без токена — открывает трек на сайте"],
    ],
    async (v) => void (await save({ service: v })),
  );
  const duck = segRow(
    [
      ["lower", "Тише", "пока Ая говорит или слушает, музыка становится тише"],
      ["pause", "Пауза", "пока Ая говорит или слушает, музыка на паузе"],
      ["off", "Не трогать", "музыка играет как есть"],
    ],
    async (v) => void (await save({ duck: v })),
  );
  const site = segRow(
    [
      ["youtube", "youtube.com", "обычный YouTube"],
      ["music", "YouTube Music", "music.youtube.com"],
    ],
    async (v) => void (await save({ youtube_site: v })),
  );
  const radioLi = el("div", "set-item");
  const radioT = el("span", "grow");
  radioT.innerHTML = `Похожие после песни<small class="dim block">микс YouTube: музыка не кончается на одном треке</small>`;
  const radioSw = switchEl(true, async (v) => void (await save({ youtube_radio: v })));
  radioLi.append(radioT, radioSw);

  const target = segRow(
    [
      ["aya", "Ая играет сама", "полные треки с Плюсом, «Моя волна», «Мне нравится» — нужен токен"],
      ["web", "Сайт", "откроет music.yandex.ru в браузере, ▶ нажимаешь ты"],
      ["app", "Приложение", "откроет приложение Яндекс Музыки (новое, с music.yandex.ru/download)"],
    ],
    async (v) => void (await save({ yandex_target: v })),
  );
  const tokenRow = el("div", "seg-row");
  const token = el("input", "inp") as HTMLInputElement;
  token.type = "password";
  token.autocomplete = "off";
  token.placeholder = "токен Яндекс Музыки";
  const saveTok = el("button", "top-btn sm", "Сохранить") as HTMLButtonElement;
  const getTok = el("button", "top-btn sm", "Получить токен") as HTMLButtonElement;
  const acc = el("div", "hint-box", "");
  const checkAcc = async () => {
    try {
      const a = await fetchJson<{ ok: boolean; login?: string; plus?: boolean; error?: string }>("/api/music/yandex/account");
      acc.textContent = a.ok
        ? `вход выполнен: ${a.login || "аккаунт"} · ${a.plus ? "Плюс есть — полные треки" : "без Плюса — Яндекс даст только отрывки"}`
        : a.error === "токен не задан"
          ? "Без токена Ая ищет в Яндексе и открывает трек на сайте. С токеном — играет сама."
          : `токен не подошёл: ${a.error}`;
    } catch {
      acc.textContent = "ядро не отвечает";
    }
  };
  saveTok.addEventListener("click", async () => {
    await save({ ym_token: token.value });
    token.value = "";
    token.placeholder = "токен сохранён — введи новый, чтобы заменить";
    toast("Токен сохранён");
    void checkAcc();
  });
  getTok.addEventListener("click", async () => {
    await postJson("/api/music/yandex/login", {});
    toast("Войди в Яндекс в браузере — токен будет в адресной строке после access_token=");
  });
  tokenRow.append(token, saveTok, getTok);

  card.append(
    now,
    el("div", "hint-box", "кого спрашивать «включи …» по умолчанию"),
    service.row,
    service.hint,
    el("div", "hint-box", "пока Ая говорит или слушает"),
    duck.row,
    duck.hint,
    el("h4", "sub-title", "YouTube"),
    site.row,
    radioLi,
    el("h4", "sub-title", "Яндекс Музыка"),
    target.row,
    target.hint,
    tokenRow,
    acc,
    el(
      "div",
      "hint-box",
      "Как получить токен: «Получить токен» → войди в Яндекс → браузер перейдёт на адрес с access_token=… — скопируй всё после «=» до «&» и вставь сюда. Токен хранится только у тебя.",
    ),
  );
  void (async () => {
    try {
      const c = await fetchJson<MusicCfg>("/api/music");
      service.mark(c.service);
      duck.mark(c.duck);
      site.mark(c.youtube_site);
      (radioSw.querySelector("input") as HTMLInputElement).checked = c.youtube_radio;
      target.mark(c.yandex_target_set || c.yandex_target);
      if (c.ym_token_set) token.placeholder = "токен сохранён — введи новый, чтобы заменить";
      const n = c.now;
      now.textContent =
        n && n.status !== "none"
          ? `${n.status === "playing" ? "Сейчас играет" : "На паузе"}: ${[n.artist, n.title].filter(Boolean).join(" — ") || "без названия"} (${n.app})`
          : "Скажи «Ая, включи Кино — Группа крови» или «Ая, пауза».";
      void checkAcc();
    } catch {
      now.textContent = "ядро не отвечает";
    }
  })();
  return card;
}

/* ── свой голос ── */
const READ_ALOUD =
  "Привет! Это мой голос. Сегодня тёплый вечер, и мне хочется поговорить о чём-нибудь хорошем: о книгах, о музыке и о планах на выходные.";

/** Запись или загрузка образца → POST /api/voices/custom. onSaved(id). */
export function voiceMaker(onSaved: (id: string, label: string) => void): HTMLElement {
  const box = el("div", "voice-maker");
  box.innerHTML = `
    <div class="field"><span>Прочитай вслух спокойно, как в разговоре (8–12 секунд)</span>
      <blockquote class="quote small">${esc(READ_ALOUD)}</blockquote></div>
    <div class="vm-row">
      <button class="btn grad" data-rec>${icon("i-mic")}Записать</button>
      <span class="vm-time" data-time>0.0 с</span>
      <label class="btn ghost vm-file">${icon("i-up")}Загрузить файл<input type="file" accept="audio/*,.wav,.mp3,.ogg,.m4a,.webm" hidden /></label>
    </div>
    <audio controls class="vm-audio hidden"></audio>
    <div class="field-grid">
      <label class="field"><span>Название</span><input class="inp" data-label maxlength="32" placeholder="Мой голос" /></label>
      <label class="field"><span>Текст образца <small>— пусто = распознаю сама</small></span><input class="inp" data-text maxlength="400" /></label>
    </div>
    <div class="row-actions"><button class="top-btn" data-save disabled>Сохранить голос</button><span class="hint" data-status></span></div>`;
  const recBtn = box.querySelector<HTMLButtonElement>("[data-rec]")!;
  const timeEl = box.querySelector<HTMLElement>("[data-time]")!;
  const audio = box.querySelector<HTMLAudioElement>("audio")!;
  const file = box.querySelector<HTMLInputElement>("input[type=file]")!;
  const save = box.querySelector<HTMLButtonElement>("[data-save]")!;
  const status = box.querySelector<HTMLElement>("[data-status]")!;
  const textIn = box.querySelector<HTMLInputElement>("[data-text]")!;
  let blob: Blob | null = null;
  let rec: MediaRecorder | null = null;
  let started = 0;
  let timer = 0;

  const setBlob = (b: Blob, fromRecording: boolean) => {
    blob = b;
    audio.src = URL.createObjectURL(b);
    audio.classList.remove("hidden");
    save.disabled = false;
    if (fromRecording && !textIn.value) textIn.value = READ_ALOUD;
  };

  recBtn.addEventListener("click", async () => {
    if (rec && rec.state === "recording") {
      rec.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const chunks: Blob[] = [];
      rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        window.clearInterval(timer);
        recBtn.innerHTML = `${icon("i-mic")}Записать заново`;
        recBtn.classList.remove("recording");
        setBlob(new Blob(chunks, { type: rec?.mimeType || "audio/webm" }), true);
      };
      rec.start();
      started = performance.now();
      recBtn.innerHTML = `${icon("i-stop")}Стоп`;
      recBtn.classList.add("recording");
      timer = window.setInterval(() => {
        const s = (performance.now() - started) / 1000;
        timeEl.textContent = `${s.toFixed(1)} с`;
        if (s >= 15) rec?.stop();
      }, 100);
    } catch (e) {
      status.textContent = `микрофон недоступен: ${String(e)}`;
    }
  });
  file.addEventListener("change", () => {
    const f = file.files?.[0];
    if (f) {
      textIn.value = "";
      setBlob(f, false);
      timeEl.textContent = f.name;
    }
  });
  save.addEventListener("click", async () => {
    if (!blob) return;
    save.disabled = true;
    status.textContent = "обрабатываю образец…";
    const form = new FormData();
    form.append("file", blob, "voice.webm");
    form.append("label", box.querySelector<HTMLInputElement>("[data-label]")!.value || "Мой голос");
    form.append("text", textIn.value);
    try {
      const r = (await (await fetch(`${CORE}/api/voices/custom`, { method: "POST", body: form })).json()) as {
        ok: boolean;
        id?: string;
        label?: string;
        error?: string;
        seconds?: number;
      };
      if (!r.ok || !r.id) {
        status.textContent = r.error || "не получилось";
        save.disabled = false;
        return;
      }
      status.textContent = `готово: ${r.seconds} с образца`;
      toast(`Голос «${r.label}» сохранён`);
      onSaved(r.id, r.label || "Мой голос");
    } catch {
      status.textContent = "ядро не отвечает";
      save.disabled = false;
    }
  });
  return box;
}

export function customVoicesCard(): HTMLElement {
  const card = el("div", "card");
  card.append(
    el("h3", "sec-title", "Свой голос"),
    el("div", "hint-box", "Ая заговорит твоим или любым другим голосом по короткому образцу (клонирование F5, всё локально). Нужна запись 8–12 секунд без музыки и шума."),
  );
  const list = el("div", "set-list");
  const load = async () => {
    list.innerHTML = "";
    const r = await fetchJson<{ custom: Voice[] }>("/api/voices").catch(() => ({ custom: [] as Voice[] }));
    if (!r.custom.length) list.append(el("div", "hint-box", "Своих голосов пока нет"));
    for (const v of r.custom) {
      const li = el("div", "set-item");
      const play = el("button", "round-btn sm") as HTMLButtonElement;
      play.innerHTML = icon("i-play");
      play.title = "Послушать";
      play.addEventListener("click", () => {
        play.classList.add("busy");
        const a = new Audio(`${CORE}/api/speak?text=${encodeURIComponent("Привет! Теперь я говорю этим голосом.")}&voice=${encodeURIComponent(v.id)}`);
        a.onplaying = a.onended = a.onerror = () => play.classList.remove("busy");
        void a.play().catch(() => play.classList.remove("busy"));
      });
      const use = el("button", "top-btn sm", "Выбрать") as HTMLButtonElement;
      use.addEventListener("click", async () => {
        await postJson("/api/config", { voice_id: v.id });
        toast(`Теперь голос «${v.label}»`);
      });
      const del = el("button", "top-btn sm danger", "Удалить") as HTMLButtonElement;
      del.addEventListener("click", async () => {
        if (!confirm(`Удалить голос «${v.label}»?`)) return;
        await postJson(`/api/voices/custom/${encodeURIComponent(v.id)}/delete`, {});
        await load();
      });
      li.append(play, el("span", "grow", v.label), use, del);
      list.append(li);
    }
  };
  const maker = voiceMaker(async (id) => {
    await postJson("/api/config", { voice_id: id });
    await load();
  });
  const details = el("details", "maker-wrap");
  details.append(el("summary", "", "Добавить голос"), maker);
  card.append(list, details);
  void load();
  return card;
}

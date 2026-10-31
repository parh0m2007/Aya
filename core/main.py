import asyncio
import contextlib
import json
import os
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))
CORE_DIR = Path.home() / ".aya"
os.makedirs(CORE_DIR, exist_ok=True)
DB_PATH = str(CORE_DIR / "aya.db")
os.chdir(Path(__file__).parent.parent)

from core import db, mcp, paths
from core import agents, bridge_telegram, bridge_discord, bridge_tguser
from core import longmem
from core import persona
from core import attach, diary, desktop, music, pomodoro
from core import gamemode, runtime, setup

runtime.activate()  # докачанные наборы (видеокарта, локальные голоса) — до импорта голоса
from core.voice import speech_plan
from core.voice.live_audio import LiveAudio
from core.reply import StreamFilter, detect_mood, parse_action
from core.behavior.state import BehaviorState
from core.behavior import proactive
from core.llm.base import friendly_error, make_adapters, stream_llm, LLMError
from core.llm import metrics as llm_metrics
from core.prompting import build_history, build_system_prompt
from core.safety import tools, gateway
from core.voice import stt, tts
from core.voice import modelmgr
from core import tasks as taskrunner

@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    mcp_boot = None
    pomodoro.recover()  # прошлый запуск закрыли посреди фокуса — снимаем «Тишину»
    if not os.environ.get("AYA_DISABLE_TASKS"):
        taskrunner.start()
        # MCP-серверы (npx …) стартуют секунды — не держим ядро, пусть догоняют в фоне
        mcp_boot = threading.Thread(target=mcp.REGISTRY.start_all, kwargs={"enabled_only": True, "silent": True}, daemon=True, name="aya-mcp-boot")
        mcp_boot.start()
        bridge_telegram.BRIDGE.start()
        bridge_discord.BRIDGE.start()
        bridge_tguser.BRIDGE.start()
        proactive.start()
        diary.start()
        music.worker.WORKER.submit(music.system.recover_duck)  # прошлый раз закрылись посреди речи
        gamemode.start()
    yield
    if not os.environ.get("AYA_DISABLE_TASKS"):
        gamemode.stop()
        with contextlib.suppress(Exception):
            music.in_worker(music.system.release_duck, timeout=3)
        if mcp_boot is not None:
            mcp_boot.join(timeout=10)  # не оставить сироту-сервер, запущенный после stop_all
        mcp.REGISTRY.stop_all()
        bridge_telegram.BRIDGE.stop()
        bridge_discord.BRIDGE.stop()
        bridge_tguser.BRIDGE.stop()
        proactive.stop()
        diary.stop()


app = FastAPI(title="Aya core", lifespan=_lifespan)
# MCP save/delete/toggle меняют mcp.json и REGISTRY; sync-эндпоинты выполняются
# в threadpool, поэтому сериализуем read-modify-write, чтобы не потерять сервер.
_MCP_LOCK = threading.Lock()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "tauri://localhost", "http://tauri.localhost", "http://127.0.0.1:8330"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DIST = Path(__file__).resolve().parent.parent / "dist"


class _Static:  # раздача консоли ядром: /console.html, /assets/*, /models/*
    @staticmethod
    def file(rel: str) -> FileResponse:
        path = (DIST / rel).resolve()
        if not str(path).startswith(str(DIST.resolve())) or not path.is_file():
            raise HTTPException(status_code=404, detail="нет файла")
        ctype = {
            ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
            ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
            ".svg": "image/svg+xml", ".wasm": "application/wasm", ".ico": "image/x-icon",
            ".moc3": "application/octet-stream", ".model3.json": "application/json",
            ".physics3.json": "application/json", ".motion3.json": "application/json",
        }.get(path.suffix, "application/octet-stream")
        return FileResponse(path, media_type=ctype)


@app.get("/")
async def console_root():
    return _Static.file("console.html")


@app.get("/console.html")
async def console_html():
    return _Static.file("console.html")


@app.get("/assets/{rest:path}")
async def console_assets(rest: str):
    return _Static.file(f"assets/{rest}")


MODEL_MEDIA_TYPES = {
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".moc3": "application/octet-stream",
}


@app.get("/models/{rest:path}")
async def console_models(rest: str):
    """Модели из ~/.aya/models, затем встроенная Haru из public/dist."""
    bases = (Path.home() / ".aya" / "models", Path("public") / "models", DIST / "models")
    for base in bases:
        base = base.resolve()
        path = (base / rest).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            continue
        if path.is_file():
            media = MODEL_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
            return FileResponse(path, media_type=media)
    raise HTTPException(status_code=404, detail="нет файла")

state = BehaviorState()
taskrunner.attach_state(state)
CONFIG_FILE = Path.home() / ".aya" / "config.json"

# новый человек: бесплатная gemma4 на OpenRouter — в мастере остаётся только вставить ключ
# (облачная Ollama с конца октября 2026 платная за каждый запрос)
DEFAULT_CONFIG = {
    "llm": {
        "type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "google/gemma-4-31b-it:free",
        "api_key": "",
    }
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))


class ChatIn(BaseModel):
    text: str = ""
    speak: bool = False
    attachments: list[str] = Field(default_factory=list)


NO_MODEL_REPLY = "Модель не настроена — открой консоль → «Мозг» и укажи модель."


@app.post("/api/chat")
async def chat(body: ChatIn):
    state.on_user_message()
    mid = db.add_message("user", body.text)
    longmem.ingest_bg("user", body.text, mid)
    cfg = load_config()
    adapters = make_adapters(cfg)
    if not adapters:
        db.add_message("aya", NO_MODEL_REPLY, "calm")
        return {"reply": NO_MODEL_REPLY, **state.snapshot()}

    system = build_system_prompt(state.snapshot(), tools=gateway.build_tool_instructions())
    history = build_history()
    try:
        raw = "".join([c async for c in stream_llm(adapters, system, history)])
    except Exception as e:
        reply = friendly_error(str(e))
        db.add_message("aya", reply, "calm")
        return {"reply": reply, **state.snapshot()}

    action, _tail = parse_action(raw)
    result = None
    if action:
        result = await gateway.dispatch(action)
        if isinstance(result, dict) and "confirm_id" in result:
            reply = "Нужно твоё подтверждение — я жду кнопку в чате."
            pending = {"id": result["confirm_id"], "tool": result["tool"], "args": result["args"]}
        else:
            reply = await compose_tool_reply(adapters, body.text, str(action.get("name", "")), result)
    else:
        reply = raw

    mid = db.add_message("assistant", reply)
    longmem.ingest_bg("assistant", reply, mid)
    state.on_reply(detect_mood(reply))
    out = {"reply": reply, **state.snapshot()}
    if action and isinstance(result, dict) and "confirm_id" in result:
        out["pending"] = pending
    return out


class ConfirmIn(BaseModel):
    id: str
    approve: bool


@app.get("/api/pending")
async def pending_list():
    """Ожидающие подтверждения (для консольного чата)."""
    if not gateway.PENDING:
        return []
    items = []
    for pid, item in list(gateway.PENDING.items()):
        if time.time() - item["ts"] > gateway.CONFIRM_TTL:
            continue
        items.append({"id": pid, "tool": item["tool"], "args": item["args"], "ts": item["ts"]})
    return items


@app.get("/api/history")
async def history(limit: int = 60):
    rows = db.recent_messages(max(10, min(200, limit)))
    return [{"role": "assistant" if r in ("assistant", "aya") else "user", "text": t} for r, t in rows]


class PendingIn(BaseModel):
    tool: str
    args: dict


COMP_SYSTEM = (
    "Ты — {name}, спутница-ассистент. Ты только что незаметно для пользователя выполнила его просьбу "
    "и теперь отвечаешь ему лично. Отвечай по-русски, кратко, на «{address}» (1–4 предложения), в своём характере: "
    "получилось ли, что именно произошло или что помешало. "
    "Результат действия может быть машинным (листинг, лог, код ошибки, таблица) — не повторяй его целиком: "
    "перескажи суть своими словами, при необходимости 2–4 конкретных пункта. "
    "Не выдумывай результат и не добавляй код, пути и команды без необходимости."
)


def _comp_system() -> str:
    """Системный промпт пересказа результата действия — с характером из конструктора.
    Раньше здесь было «кратко и тепло» для любого характера."""
    prof = persona.load()
    text = COMP_SYSTEM.replace("{name}", prof["name"]).replace("{address}", prof["address"])
    if prof.get("configured"):
        lines = "\n".join("- " + line for line in persona.character_lines(prof))
        return text + "\n\nТвой характер (держи его и здесь):\n" + lines + "\n" + persona.reminder(prof)
    return text + "\n\nТвой характер: тёплая, заботливая, с мягким юмором."


async def _compose_chunks(adapters, user_text: str, tool_name: str, result):
    """Человеческий пересказ результата инструмента; при сбое — простой fallback."""
    snippet = str(result).strip()[:3500]
    prompt = (
        f"Просьба пользователя: «{str(user_text).strip()[:300]}»\n"
        f"Ты выполнила инструмент «{tool_name}». Реальный результат выполнения:\n"
        f"---\n{snippet}\n---\n\nОтветь пользователю от себя лично."
    )
    fallback = "Готово: " + str(result)[:400]
    got = ""
    try:
        system = _comp_system()
        async for chunk in stream_llm(adapters, system, [{"role": "user", "content": prompt}]):
            got += chunk
    except Exception:
        if got.strip():
            got += "\n(немного сбилась, исходный результат: " + str(result)[:200] + ")"
        else:
            got = fallback
    yield got.strip() or fallback


async def compose_tool_reply(adapters, user_text: str, tool_name: str, result) -> str:
    parts = [p async for p in _compose_chunks(adapters, user_text, tool_name, result)]
    return "".join(parts)


@app.post("/api/action/pending")
async def action_pending(body: PendingIn):
    """Дев-эндпоинт: создать pending на подтверждение (используется тестами/упаковщиком)."""
    if body.tool not in gateway.CONFIRM_TOOLS:
        return {"ok": False, "error": "tool not confirmable"}
    res = await gateway.dispatch({"name": body.tool, "args": body.args})
    if isinstance(res, dict) and "confirm_id" in res:
        return {"ok": True, **res}
    return {"ok": False, "error": str(res)[:120]}


@app.post("/api/action/confirm")
async def action_confirm(body: ConfirmIn):
    res = await asyncio.to_thread(gateway.resolve_confirm, body.id, body.approve)
    if res is None:
        return {"ok": False, "result": "такого ожидания нет"}
    if res.get("applied") and not res.get("expired"):
        adapters = make_adapters(load_config())
        if adapters:
            row = db.query("SELECT text FROM messages WHERE role='user' ORDER BY id DESC LIMIT 1")
            user_text = str(row[0][0]) if row else ""
            reply = await compose_tool_reply(adapters, user_text, str(res.get("tool") or ""), res.get("result", ""))
            res["result"] = reply
            mid = db.add_message("assistant", reply)
            longmem.ingest_bg("assistant", reply, mid)
    return {"ok": True, **res}


class WorkspaceIn(BaseModel):
    folder: str


def _save_llm_section(key: str, base_url: str, model: str, ltype: str, api_key: str) -> dict:
    """Обновить секцию llm/llm_agents в ~/.aya/config.json (api_key пустой = оставить как был)."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    sec = dict(cfg.get(key) or {})
    if base_url:
        sec["base_url"] = base_url.strip()
    if model:
        sec["model"] = model.strip()
    if ltype:
        sec["type"] = ltype.strip()
    if api_key:
        sec["api_key"] = api_key.strip()
    sec.setdefault("type", "openai_compatible")
    cfg[key] = sec
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return sec


def _llm_view(sec: dict | None) -> dict:
    sec = sec or {}
    key = str(sec.get("api_key") or "")
    return {
        "type": sec.get("type", ""),
        "base_url": sec.get("base_url", ""),
        "model": sec.get("model", ""),
        "api_key_set": bool(key),
        "api_key_tail": (key[:4] + "…" + key[-4:]) if len(key) > 12 else ("…" if key else ""),
    }


class LlmProfile(BaseModel):
    type: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""


class LlmIn(BaseModel):
    llm: LlmProfile | None = None
    llm_agents: LlmProfile | None = None
    llm_vision: LlmProfile | None = None


@app.get("/api/llm")
async def llm_get():
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    return {
        "llm": _llm_view(cfg.get("llm")),
        "llm_agents": _llm_view(cfg.get("llm_agents")),
        "llm_vision": _llm_view(cfg.get("llm_vision")),
    }


@app.post("/api/llm")
async def llm_set(body: LlmIn):
    out = {}
    for key, prof in (("llm", body.llm), ("llm_agents", body.llm_agents), ("llm_vision", body.llm_vision)):
        if prof is not None:
            sec = _save_llm_section(key, prof.base_url, prof.model, prof.type, prof.api_key)
            out[key] = _llm_view(sec)
    db.log_event("config", f"llm set: {', '.join(out.keys()) or 'пусто'}")
    return {"ok": True, **out}


@app.get("/api/workspace")
async def workspace_get():
    return {"folder": db.setting("allowed_folder", "")}


@app.post("/api/workspace")
async def workspace_set(body: WorkspaceIn):
    folder = body.folder.strip()
    if folder and not os.path.isdir(folder):
        return {"ok": False, "error": "такой папки нет"}
    db.set_setting("allowed_folder", folder)
    return {"ok": True, "folder": folder}


_BG_TASKS: set = set()


async def _no_stream():
    """Пустой поток вместо модели (для быстрых команд)."""
    return
    yield ""


def _bg(coro) -> None:
    """Фоновая задача без ожидания; ссылку держим, чтобы её не собрал GC."""
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn):
    """NDJSON stream: {"delta": text}* / {"speech": {...}}* then {"done": true, reply, mood, activity}.

    speak=true — ядро само режет ответ на фразы и озвучивает их по мере генерации;
    окно получает события speech с id готовящихся кусков (/api/speech/{id}).
    attachments — пути файлов, брошенных на аватар.
    """
    state.on_user_message()
    files = []
    paths = attach.safe_paths(body.attachments)
    if paths:
        files = await asyncio.gather(*[asyncio.to_thread(attach.read_attachment, p) for p in paths])
    stored = body.text if not files else (f"{body.text}\n" if body.text.strip() else "") + attach.short_label(files)
    mid = db.add_message("user", stored)
    longmem.ingest_bg("user", stored, mid)
    cfg = load_config()
    adapters = make_adapters(cfg)
    if not adapters:
        db.add_message("aya", NO_MODEL_REPLY, "calm")

        async def no_model():
            # фронт читает NDJSON и ждёт строку done — обычный JSON ломал «стрим оборвался»
            yield json.dumps({"delta": NO_MODEL_REPLY}, ensure_ascii=False) + "\n"
            yield json.dumps({"done": True, "reply": NO_MODEL_REPLY, **state.snapshot()}, ensure_ascii=False) + "\n"

        return StreamingResponse(no_model(), media_type="application/x-ndjson")

    system = build_system_prompt(state.snapshot(), tools=gateway.build_tool_instructions())
    history = build_history()
    if files and history and history[-1].get("role") == "user":
        # содержимое файлов — только в этот ход; в истории остаётся короткая метка
        history[-1] = {"role": "user", "content": attach.build_prompt(body.text, files)}

    plan = None
    cloud = False
    if body.speak and db.setting("tts_enabled", "1") != "0":
        mood = state.snapshot().get("mood", "calm")
        cloud = tts.is_cloud() and tts.fish_ready()

        async def synth(text: str, m: str, fast: bool):
            if cloud:
                live = await tts.speak_live(text, m)
                if live is not None:
                    return live
            path, _hit = await tts.speak(text, m, fast=fast)
            return path

        # облаку — три фразы сразу, локальной видеокарте — по одной
        plan = speech_plan.SpeechPlan(mood, synth, fast_first=True, parallel=3 if cloud else 1)
        if cloud:
            _bg(tts.fish_warm_connection())  # TLS к Fish, пока модель думает

    def speech_events(pieces) -> str:
        return "".join(
            json.dumps({"speech": {"id": p.id, "seq": p.seq, "plan": plan.id, "stream": cloud}}, ensure_ascii=False) + "\n"
            for p in pieces
        )

    # «пауза», «следующий трек», «что играет» — сразу инструмент, без первого
    # прохода модели: быстрее, и модель не «забудет» нажать (по истории ей
    # иногда кажется, что музыка уже на паузе)
    quick = music.quick_intent(body.text) if not files else None

    async def gen():
        parts: list[str] = []
        err_text = ""
        action = None
        result = None
        filt = StreamFilter()  # обычный текст идёт наружу сразу, JSON-действие прячется
        if files:
            yield json.dumps({"files": [{"name": f["name"], "kind": f["kind"], "error": f["error"]} for f in files]}, ensure_ascii=False) + "\n"
        try:
            async for chunk in (_no_stream() if quick else stream_llm(adapters, system, history)):
                parts.append(chunk)
                out = filt.feed(chunk)
                if out:
                    yield json.dumps({"delta": out}, ensure_ascii=False) + "\n"
                    if plan:
                        ev = speech_events(plan.feed(out))
                        if ev:
                            yield ev
            out = filt.flush()
            if out:
                yield json.dumps({"delta": out}, ensure_ascii=False) + "\n"
                if plan:
                    ev = speech_events(plan.feed(out))
                    if ev:
                        yield ev
        except Exception as e:
            err_text = str(e)
            yield json.dumps({"error": friendly_error(err_text)}, ensure_ascii=False) + "\n"

        if err_text:
            reply = friendly_error(err_text)
        else:
            raw = "".join(parts)
            action, _tail = ({"name": "music", "args": quick}, "") if quick else parse_action(raw)
            if action:
                result = await gateway.dispatch(action)
                if isinstance(result, dict) and "confirm_id" in result:
                    reply = "Нужно твоё подтверждение — я жду кнопку в чате."
                    pending = {"id": result["confirm_id"], "tool": result["tool"], "args": result["args"]}
                else:
                    pieces: list[str] = []
                    async for chunk in _compose_chunks(adapters, body.text, str(action.get("name", "")), result):
                        pieces.append(chunk)
                        if chunk.strip():
                            yield json.dumps({"delta": chunk}, ensure_ascii=False) + "\n"
                            if plan:
                                ev = speech_events(plan.feed(chunk))
                                if ev:
                                    yield ev
                    reply = "".join(pieces).strip() or f"Готово: {str(result)[:400]}"
            else:
                reply = raw
        if plan:
            if err_text or (isinstance(result, dict) and "confirm_id" in result):
                plan.cancel()
            else:
                ev = speech_events(plan.finish())
                if ev:
                    yield ev
        mid = db.add_message("assistant", reply)
        longmem.ingest_bg("assistant", reply, mid)
        state.on_reply(detect_mood(reply) if not err_text else "")
        done = {"done": True, "reply": reply, **state.snapshot(), "music": music.player.snapshot()}
        if plan:
            done["speech_plan"] = plan.id
        if err_text:
            done["error"] = err_text
        if not err_text and isinstance(action, dict) and isinstance(result, dict) and "confirm_id" in result:
            # фронт ждёт {id, tool, args} — раньше улетал сырой dict с confirm_id и кнопка ломалась
            done["pending"] = pending
        yield json.dumps(done, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.get("/api/speech/{piece_id}")
async def speech_piece(piece_id: str):
    """Кусок озвучки из плана хода. Облачный звук отдаётся потоком, по мере
    синтеза; локальный — готовым файлом (запрос ждёт, пока синтез закончится)."""
    path = await speech_plan.wait_piece(piece_id)
    if isinstance(path, LiveAudio):
        live = path
        if await live.ready():
            return StreamingResponse(
                live.iter_bytes(), media_type=live.media, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )
        path = live.path
    if not path:
        raise HTTPException(status_code=404, detail="озвучки нет")
    fmt = "audio/mpeg" if str(path).endswith(".mp3") else "audio/wav"
    return FileResponse(path, media_type=fmt)


class SpeechCancelIn(BaseModel):
    plan: str


@app.post("/api/speech/cancel")
async def speech_cancel(body: SpeechCancelIn):
    return {"ok": speech_plan.cancel(body.plan)}


@app.get("/api/state")
async def get_state():
    return state.snapshot()


@app.post("/api/state")
async def touch_state():
    state.on_user_message()
    return state.snapshot()


INIT_FALLBACK = {
    "welcome_back": "О, ты вернулся! Как всё прошло?",
    "light_ping": "Тут как-то тихо стало… Как у тебя дела?",
    "morning": "С добрым утром! Как спалось?",
    "break": "Может, перерыв? Я пока побуду рядом.",
    "idle": "Ты тут? Я не отвлекаю, просто скучаю немного.",
    "digest": "День подошёл к концу… Что-нибудь рассказать о нём?",
}

INIT_PROMPTS = INIT_FALLBACK  # базовые темы проговариваются простым текстом

TG_RETRY_DELAY = 90  # сек: пауза перед повтором недоставленных писем Telegram


def _init_prompt(topic: str, tg_items: list[dict] | None = None) -> str:
    if topic.startswith("reminder:"):
        note = topic.split(":", 1)[1][:180]
        return (
            f"Пришло время напоминания, которое попросил пользователь: «{note}». "
            "Скажи об этом коротко, в своём характере, одним предложением-двумя. "
            "Не задавай вопросов, просто напомни."
        )
    if topic.startswith("report:"):
        note = topic.split(":", 1)[1][:900]
        return (
            "Прилетел отчёт от фонового суб-агента, который работал по твоему поручению. "
            "Перескажи пользователю его итог своими словами, кратко и в своём характере (пара предложений):\n" + note
        )
    if topic.startswith("tg:"):
        items = tg_items if tg_items is not None else bridge_tguser.peek_inbox(8)
        text = "\n".join(bridge_tguser.format_inbox(items[:8])) or "в списке пусто"
        mode = bridge_tguser.mode()
        base = (
            "Пользователю пришли новые личные сообщения в Telegram:\n" + text + "\n\n"
        )
        if "[голосовое" in text:
            base += (
                "Пометка «[голосовое]» — это расшифровка голосового сообщения; "
                "перескажи её смысл так же, как обычный текст.\n"
            )
        if mode == "draft":
            base += (
                "Коротко перескажи пользователю, кто и что написал (1–2 предложения), и предложи "
                "одной фразой, как можно ответить от его имени. Если он захочет отправить — он попросит "
                "об этом в чате. Сейчас ничего не отправляй и не вызывай инструменты."
            )
        elif mode == "auto":
            base += (
                "Коротко перескажи пользователю, кто и что написал (1–2 предложения). Что ответить — "
                "пользователь решит в чате сам, сейчас ничего не отправляй и не вызывай инструменты."
            )
        else:
            base += "Просто перепиши пользователю, кто и что написал, без отправки ответов."
        return base
    if topic == "morning":
        return (
            "Новое утро началось. Поприветствуй пользователя в своём характере, одно-два предложения. "
            "Не задавай много вопросов, просто покажись."
        )
    if topic == "break":
        app = proactive.current().get("app", "")
        where = f" Ты уже много часов не выходил из «{app}»." if app else ""
        return (
            "Пользователь давно сидит в одном приложении без перерыва." + where + " "
            "Предложи передохнуть: чаю, размяться, глянуть в окно. В своём характере, без нотаций, 1-2 предложения."
        )
    if topic == "idle":
        return (
            "Пользователь давно не трогал клавиатуру. Коротко поинтересуйся, всё ли в порядке — "
            "может он ушёл, а может монотонным делом занят. Одно предложение, в своём характере."
        )
    if topic == "digest":
        stats_txt = proactive.summary_text()
        n_msg = db.query("SELECT COUNT(*) FROM messages WHERE ts>=?", (time.time() - 86400,))
        cnt = int(n_msg[0][0]) if n_msg else 0
        return (
            "Вечер. Расскажи пользователю короткий итог дня: "
            f"{stats_txt} "
            f"За сутки в чате у вас было около {cnt} сообщений. "
            "Подай в своём характере: чем занимались, что было важно. До 4 предложений."
        )
    return INIT_PROMPTS.get(topic, topic)


@app.get("/api/initiative")
async def initiative(requested_only: bool = False):
    """requested_only — окно с выключенной инициативой: только напоминания и отчёты."""
    if gamemode.active():  # в игре сама не заговаривает (и маркеры отложенных писем не трогаем)
        return {"topic": "", "reply": "", **state.snapshot()}
    topic = state.initiative_check(requested_only=True) if requested_only else state.initiative_check()
    out = {"topic": "", "reply": "", **state.snapshot()}
    if not topic:
        return out
    tg_items = []
    if topic.startswith("tg:"):
        dropped = bridge_tguser.prune_inbox()
        if dropped:
            db.log_event("telegram_user", f"пропущено старых писем: {dropped}")
        tg_items = bridge_tguser.peek_inbox(8)
        if not tg_items:
            return out  # свежих писем нет — доставлять нечего, форс-маркер уже снят
    db.add_message("event", f"initiative:{topic}")
    cfg = load_config()
    adapters = make_adapters(cfg)
    gen_error = ""
    try:
        system = build_system_prompt(state.snapshot())
        history = build_history(verbatim=4)
        prompt = _init_prompt(topic, tg_items)
        raw = "".join(
            [
                c
                async for c in stream_llm(
                    adapters,
                    system + "\n\nСитуация: " + prompt,
                    history,
                    first_timeout=60,  # фоновой инициативе можно подождать облако
                )
            ]
        )
        reply = raw.strip().splitlines()[0] if raw.strip() else ""
    except Exception as e:  # noqa: BLE001
        gen_error = f"{type(e).__name__}: {str(e)[:140] or llm_metrics.last_fallback() or 'пустой ответ'}"
        reply = ""
    if not reply:
        if tg_items:
            # Генерация не удалась: письма НЕ сжигаем, вернёмся к ним после паузы.
            db.set_setting("initiative_force", f"tg:retry:{int(time.time()) + TG_RETRY_DELAY}")
            db.log_event(
                "telegram_user",
                f"инициатива tg: не сгенерировалась ({gen_error or 'пустой ответ'}) — {len(tg_items)} писем ждут повтора",
            )
            return out
        if topic.startswith("reminder:"):
            reply = "Напоминание: " + topic.split(":", 1)[1]
        elif topic.startswith("report:"):
            reply = topic.split(":", 1)[1][:600]  # модель молчит — отчёт отдаём как есть
        else:
            reply = INIT_FALLBACK.get(topic, INIT_FALLBACK["light_ping"])
    elif tg_items:
        bridge_tguser.drop_inbox(tg_items)  # доставлено — подтверждаем
        if bridge_tguser.peek_inbox(1):
            # в ящике остался ещё хвост — дошлём следующей порцией
            db.set_setting("initiative_force", f"tg:retry:{int(time.time()) + 45}")
    if not topic.startswith("tg:") and not db.setting("initiative_force", "") and bridge_tguser.peek_inbox(1):
        # письма ждали, пока приоритетный маркер (напоминание/отчёт) занимал очередь
        db.set_setting("initiative_force", f"tg:retry:{int(time.time()) + 30}")
    db.add_message("assistant", reply)
    state.on_reply(detect_mood(reply))
    return {"topic": topic, "reply": reply, **state.snapshot()}


@app.get("/api/memory")
async def memory():
    return {"facts": [{"key": k, "value": v} for k, v, _ in db.facts()]}


class FactIn(BaseModel):
    key: str
    value: str


class ForgetIn(BaseModel):
    key: str


@app.post("/api/memory/add")
async def memory_add(body: FactIn):
    db.add_fact(body.key[:40], body.value[:200], "user")
    return {"ok": True}


@app.post("/api/memory/forget")
async def forget(body: ForgetIn):
    db.forget_fact(body.key)
    return {"ok": True}


@app.get("/api/audit")
async def audit():
    rows = db.query("SELECT ts,tool,args,result,allowed FROM audit_log ORDER BY id DESC LIMIT 100")
    return [{"ts": r[0], "tool": r[1], "args": r[2], "result": r[3], "allowed": bool(r[4])} for r in rows]


class PermIn(BaseModel):
    tool: str
    enabled: bool


@app.post("/api/permissions")
async def permissions(body: PermIn):
    tools.allow_tool(body.tool, body.enabled)
    return {"ok": True}


@app.get("/api/permissions")
async def get_permissions():
    return {n: tools.is_tool_enabled(n) for n in tools.KNOWN_ACTIONS}


class AllPermIn(BaseModel):
    enabled: bool


@app.post("/api/permissions/all")
async def permissions_all(body: AllPermIn):
    """Полное разрешение: включить/выключить все инструменты сразу."""
    tools.set_all_tools(body.enabled)
    db.log_event("config", f"permissions all: {'enabled' if body.enabled else 'disabled'}")
    return {"ok": True, **{n: body.enabled for n in tools.KNOWN_ACTIONS}}


@app.get("/api/mcp")
async def mcp_status():
    servers = mcp.REGISTRY.config().get("servers") or {}
    status = mcp.REGISTRY.status()
    return {
        name: {
            "command": str(spec.get("command", "")),
            "args": [str(a) for a in (spec.get("args") or [])],
            "env": {str(k): str(v) for k, v in spec.get("env", {}).items()} if isinstance(spec, dict) and isinstance(spec.get("env"), dict) else {},
            "cwd": str(spec.get("cwd") or "") if isinstance(spec, dict) else "",
            "enabled": bool(spec.get("enabled", False)) if isinstance(spec, dict) else False,
            "permitted": mcp.permitted(name),
            "connected": status.get(name, {}).get("connected", False),
            "error": status.get(name, {}).get("error", "") or mcp.REGISTRY.errors.get(name, ""),
            "tools": status.get(name, {}).get("tools", []),
        }
        for name, spec in servers.items()
    }


class McpToggleIn(BaseModel):
    server: str


@app.post("/api/mcp/toggle")
def mcp_toggle(body: McpToggleIn):
    server = str(body.server or "").strip()
    with _MCP_LOCK:
        cfg = mcp.REGISTRY.config()
        servers = cfg.setdefault("servers", {})
        spec = servers.get(server)
        if not isinstance(spec, dict):
            raise HTTPException(status_code=404, detail=f"нет сервера {server} в mcp.json")
        spec["enabled"] = not spec.get("enabled", False)
        try:
            norm = mcp.validate_spec(server, spec)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        mcp.write_config(cfg)
        if spec["enabled"]:
            error = mcp.start_registered(server, norm)
            db.log_event("mcp", f"подключён сервер {server}" if not error else f"ошибка сервера {server}: {error}")
        else:
            mcp.stop_registered(server)
            error = ""
            db.log_event("mcp", f"отключён сервер {server}")
    return {"ok": True, "enabled": spec["enabled"], "error": error}


class McpServerIn(BaseModel):
    name: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    enabled: bool = False


class McpSaveIn(McpServerIn):
    old_name: str = ""


class McpDeleteIn(BaseModel):
    name: str


@app.post("/api/mcp/save")
def mcp_save(body: McpSaveIn):
    name = str(body.name or "").strip()
    old = str(body.old_name or "").strip()
    with _MCP_LOCK:
        try:
            spec = mcp.validate_spec(name, body.model_dump())
            mcp.save_server(name, spec, old)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if old and old != name:
            mcp.stop_registered(old)

        error = ""
        if spec["enabled"]:
            error = mcp.start_registered(name, spec)
        else:
            mcp.stop_registered(name)
        db.log_event("mcp", f"сохранён сервер {name}")
    return {"ok": True, "enabled": spec["enabled"], "error": error}


@app.post("/api/mcp/delete")
def mcp_delete(body: McpDeleteIn):
    name = str(body.name or "").strip()
    with _MCP_LOCK:
        if not mcp.delete_server(name):
            raise HTTPException(status_code=404, detail=f"нет сервера {name}")
        mcp.stop_registered(name)
        db.log_event("mcp", f"удалён сервер {name}")
    return {"ok": True}


@app.post("/api/mcp/test")
def mcp_test(body: McpServerIn):
    name = str(body.name or "").strip()
    try:
        return mcp.test_server(name, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class McpPermIn(BaseModel):
    enabled: bool


@app.post("/api/mcp/perm/{server}")
async def mcp_perm(server: str, body: McpPermIn):
    mcp.set_permitted(server.strip(), body.enabled)
    return {"ok": True}


@app.get("/api/telegram")
async def telegram_status():
    return bridge_telegram.BRIDGE.status()


@app.get("/api/discord")
async def discord_status():
    return bridge_discord.BRIDGE.status()


class DiscordIn(BaseModel):
    token: str = ""
    channel_id: str = ""
    enabled: bool


@app.post("/api/discord")
async def discord_set(body: DiscordIn):
    bridge_discord.save_config(body.token.strip(), body.channel_id.strip(), body.enabled)
    db.log_event("discord", f"конфиг: enabled={body.enabled} канал={body.channel_id}")
    bridge_discord.BRIDGE.restart()
    return bridge_discord.BRIDGE.status()


# ── Проактивность 2.0 ────────────────────────────────────────────────
class ProactiveIn(BaseModel):
    enabled: bool | None = None
    focus: bool | None = None
    digest: bool | None = None


@app.get("/api/proactive")
async def proactive_status():
    return proactive.status()


@app.post("/api/proactive")
async def proactive_set(body: ProactiveIn):
    if body.enabled is not None:
        db.set_setting("proactive_enabled", "1" if body.enabled else "0")
    if body.focus is not None:
        db.set_setting("focus_mode", "1" if body.focus else "0")
    if body.digest is not None:
        db.set_setting("digest_enabled", "1" if body.digest else "0")
    db.log_event("config", f"proactive: {body.model_dump()}")
    return proactive.status()


# ── Telegram: твой аккаунт (юзербот на Telethon) ─────────────────────
class TgUserIn(BaseModel):
    enabled: bool | None = None
    mode: str | None = None
    contacts: list[str] | None = None
    private_only: bool | None = None
    proxy: dict | None = None  # {} = убрать прокси, {type, host, port, secret?} = задать


class TgLoginPhone(BaseModel):
    api_id: int
    api_hash: str
    phone: str
    proxy: dict | None = None  # {type: socks5|mtproto, host, port, secret?}


class TgLoginCode(BaseModel):
    code: str


class TgLoginPassword(BaseModel):
    password: str


@app.get("/api/tguser")
async def tguser_status():
    return bridge_tguser.BRIDGE.status()


@app.post("/api/tguser")
async def tguser_set(body: TgUserIn):
    if body.mode is not None and body.mode not in ("report", "draft", "auto"):
        return {"ok": False, "error": "mode должен быть report/draft/auto", **bridge_tguser.BRIDGE.status()}
    patch = {
        k: v
        for k, v in (
            ("enabled", body.enabled),
            ("mode", body.mode),
            ("contacts", body.contacts),
            ("private_only", body.private_only),
            ("proxy", body.proxy),
        )
        if v is not None
    }
    cfg = bridge_tguser.save_config(**patch)
    if body.enabled:
        # фича включена — отправка от лица юзера разрешена (в draft всё равно нужна кнопка)
        tools.allow_tool("tg_send", True)
    db.log_event("telegram_user", f"конфиг: mode={cfg['mode']} контактов={len(cfg['contacts'])} enabled={cfg['enabled']}")
    bridge_tguser.BRIDGE.restart()
    s = bridge_tguser.BRIDGE.status()
    s["ok"] = True
    return s


@app.post("/api/tguser/login")
async def tguser_login(body: TgLoginPhone):
    res = await asyncio.to_thread(bridge_tguser.LOGIN.phone, body.api_id, body.api_hash.strip(), body.phone.strip(), body.proxy)
    return res


@app.post("/api/tguser/code")
async def tguser_code(body: TgLoginCode):
    res = await asyncio.to_thread(bridge_tguser.LOGIN.code, body.code.strip())
    return res


@app.post("/api/tguser/password")
async def tguser_password(body: TgLoginPassword):
    res = await asyncio.to_thread(bridge_tguser.LOGIN.password, body.password)
    return res


@app.post("/api/tguser/logout")
async def tguser_logout():
    bridge_tguser.BRIDGE.stop()
    bridge_tguser.save_config(session="", enabled=False)
    return bridge_tguser.BRIDGE.status()


@app.get("/api/agents")
async def agents_list():
    return {"active": agents.active(), "agents": agents.list_agents()}


class AgentSelectIn(BaseModel):
    name: str = ""


@app.post("/api/agents/select")
async def agents_select(body: AgentSelectIn):
    ok, msg = agents.set_active(body.name.strip())
    return {"ok": ok, "message": msg, "active": agents.active()}


class AgentCreateIn(BaseModel):
    name: str
    soul: str = ""


@app.post("/api/agents/create")
async def agents_create(body: AgentCreateIn):
    ok, msg = agents.create(body.name, body.soul)
    return {"ok": ok, "message": msg}


@app.post("/api/agents/soul/{name}")
async def agents_soul_write(name: str, body: AgentCreateIn):
    ok, msg = agents.write_soul(name, body.soul)
    return {"ok": ok, "message": msg}


@app.get("/api/agents/soul/{name}")
async def agents_soul_read(name: str):
    if not agents.safe_name(name):
        raise HTTPException(status_code=404, detail="нет такой личности")
    return {"soul": agents.agent_soul(name)}


class TelegramIn(BaseModel):
    token: str = ""
    chat_id: str = ""
    enabled: bool


@app.post("/api/telegram")
async def telegram_set(body: TelegramIn):
    bridge_telegram.save_tg_config(body.token.strip(), body.chat_id.strip(), body.enabled)
    db.log_event("telegram", f"конфиг: enabled={body.enabled} chat={body.chat_id}")
    bridge_telegram.BRIDGE.restart()
    return bridge_telegram.BRIDGE.status()


@app.post("/api/voice/transcribe")
async def voice_transcribe(file: UploadFile):
    data = await file.read()
    text = await asyncio.to_thread(stt.transcribe, data)
    return {"text": text}


def _rotate_log(path: Path, limit: int = 2_000_000) -> None:
    try:
        if path.exists() and path.stat().st_size > limit:
            path.with_suffix(path.suffix + ".1").write_bytes(path.read_bytes())
            path.write_bytes(b"")
    except Exception:
        pass


class ModelIn(BaseModel):
    name: str


def _model_file(name: str) -> str:
    base = Path.home() / ".aya" / "models"
    src = (base / name).resolve()
    try:
        src.relative_to(base.resolve())
    except ValueError:
        return ""
    if not src.is_dir():
        return ""
    hunt = sorted(src.glob("*.model3.json")) or sorted(src.glob("*/*.model3.json"))
    if not hunt:
        return ""
    rel = hunt[0].relative_to(src)
    return f"{name}/{rel.as_posix()}"


@app.get("/api/models")
async def models_list():
    """Live2D-модели из ~/.aya/models: {name: {active, file}}."""
    base = Path.home() / ".aya" / "models"
    active = db.setting("live2d_model", "")
    out: dict[str, dict] = {}
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and _model_file(d.name):
                out[d.name] = {"active": d.name == active, "file": _model_file(d.name)}
    return out


@app.post("/api/models")
async def model_select(body: ModelIn):
    file = _model_file(body.name)
    if not file:
        return {"ok": False, "error": "модель не найдена"}
    db.set_setting("live2d_model", body.name)
    db.set_setting("live2d_model_file", file)
    return {"ok": True, "file": file}
@app.get("/api/debug/metrics")
async def debug_metrics():
    # в потоке: подсчёт кэша и опрос GPU не должны замораживать event loop
    return await asyncio.to_thread(_metrics_sync)


def _metrics_sync() -> dict:
    out: dict = {
        "engines": tts.engines_loaded(),
        "touched": modelmgr.status(),
        "unload_min": modelmgr.idle_minutes(),
        "svc": llm_metrics.snapshot(),
    }
    try:
        # torch импортируем, только если его уже загрузил голосовой движок:
        # холодный import torch — 10+ секунд, и раньше всё ядро ждало его
        torch = sys.modules.get("torch")
        if torch is None or not torch.cuda.is_available():
            raise RuntimeError("torch не загружен")
        props = torch.cuda.get_device_properties(0)
        out["vram_allocated_gb"] = round(torch.cuda.memory_allocated(0) / 2**30, 2)
        out["vram_reserved_gb"] = round(torch.cuda.memory_reserved(0) / 2**30, 2)
        out["vram_total_gb"] = round(props.total_memory / 2**30, 1)
    except Exception:
        pass
    try:
        total = sum(p.stat().st_size for p in tts._cache_dir.glob("*") if p.is_file())
        out["tts_cache_mb"] = round(total / 2**20, 1)
    except Exception:
        pass
    return out


@app.post("/api/debug/unload")
async def debug_unload():
    done = await asyncio.to_thread(modelmgr.unload_vram)
    return {"ok": True, "unloaded": done, "metrics": await debug_metrics()}


@app.post("/api/_diag")
async def tts_diag(payload: dict):
    """Временный diagnostic-приёмник с фронта (состояние TTS-плеера)."""
    logp = paths.logs_dir() / "tts-diag.log"
    _rotate_log(logp)
    try:
        with logp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/speak")
async def speak_endpoint(text: str, mood: str = "calm", voice: str = "", traits: str = ""):
    """traits — характер с ползунков конструктора (JSON), чтобы «Послушать» звучало им до сохранения."""
    from fastapi import HTTPException

    style = None
    if traits:
        try:
            style = persona.voice_style_for(json.loads(traits))
        except (ValueError, TypeError):
            style = None
    path, hit = await tts.speak(text, mood, vid_override=voice, style=style)
    if not path:
        raise HTTPException(status_code=204, detail="empty text")
    fmt = "audio/mpeg" if path.endswith(".mp3") else "audio/wav"
    return FileResponse(path, media_type=fmt, headers={"X-Cache": str(hit)})


class VoiceIn(BaseModel):
    wake_word: bool | None = None
    wake_names: str | None = None
    avatar_walk: bool | None = None
    diary_enabled: bool | None = None
    tts_fast_first: bool | None = None
    fish_latency: str | None = None
    fish_api_key: str | None = None
    tts_enabled: bool | None = None
    avatar_reactions: bool | None = None
    avatar_idle_life: bool | None = None
    avatar_scale: float | None = None
    voice_id: str | None = None
    tts_volume: int | None = None
    voice_mode: str | None = None
    initiative: bool | None = None
    confirm_mode: str | None = None
    followup: bool | None = None
    followup_sec: int | None = None
    hotkey_talk: str | None = None
    hotkey_toggle: str | None = None
    game_mode: bool | None = None


HOTKEY_DEFAULTS = {"hotkey_talk": "Control+Space", "hotkey_toggle": "Alt+Shift+A"}


def _followup_seconds() -> int:
    try:
        value = int(db.setting("voice_followup_sec", "6"))
    except ValueError:
        value = 6
    return max(2, min(30, value))


@app.get("/api/config")
async def get_config():
    return {
        "tts_voice_id": db.setting("tts_voice_id", tts.DEFAULT_VOICE),
        "tts_volume": int(db.setting("tts_volume", "100")),
        "live2d_model_file": db.setting("live2d_model_file", "haru/Haru.model3.json"),
        "voice_mode": db.setting("voice_mode", "button") if db.setting("voice_mode", "button") in ("button", "auto", "hotkey") else "button",
        "initiative": db.setting("aya_initiative", "1") == "1",
        "confirm_mode": tools.confirm_mode(),
        "followup": db.setting("voice_followup", "0") == "1",
        "followup_sec": _followup_seconds(),
        **{k: db.setting(k, v) or v for k, v in HOTKEY_DEFAULTS.items()},
        "tts_enabled": db.setting("tts_enabled", "1") != "0",
        "avatar_reactions": db.setting("avatar_reactions", "1") != "0",
        "avatar_idle_life": db.setting("avatar_idle_life", "1") != "0",
        "avatar_scale": _avatar_scale(),
        "persona_name": persona.name(),
        "wake_word": db.setting("wake_word", "0") == "1",
        "wake_names": db.setting("wake_names", ""),
        "avatar_walk": db.setting("avatar_walk", "0") == "1",
        "diary_enabled": db.setting("diary_enabled", "1") != "0",
        "tts_fast_first": db.setting("tts_fast_first", "1") != "0",
        "fish_latency": tts.fish_latency(),
        "fish_key_set": bool(db.setting("fish_api_key", "").strip()),  # сам ключ наружу не отдаём
        "game_mode": gamemode.enabled(),
    }


def _avatar_scale() -> float:
    try:
        return max(0.5, min(1.6, float(db.setting("avatar_scale", "1"))))
    except ValueError:
        return 1.0


@app.post("/api/config")
async def set_config(body: VoiceIn):
    if body.voice_id:
        db.set_setting("tts_voice_id", body.voice_id)
    if body.tts_volume is not None:
        db.set_setting("tts_volume", str(max(0, min(300, body.tts_volume))))
    if body.voice_mode is not None and body.voice_mode in ("button", "auto", "hotkey"):
        db.set_setting("voice_mode", body.voice_mode)
    if body.initiative is not None:
        db.set_setting("aya_initiative", "1" if body.initiative else "0")
    if body.confirm_mode is not None and body.confirm_mode in ("ask", "auto"):
        db.set_setting("perm_mode", body.confirm_mode)
        db.log_event("config", f"confirm_mode: {body.confirm_mode}")
    if body.followup is not None:
        db.set_setting("voice_followup", "1" if body.followup else "0")
    if body.followup_sec is not None:
        db.set_setting("voice_followup_sec", str(max(2, min(30, body.followup_sec))))
    for key in ("wake_word", "avatar_walk", "diary_enabled", "tts_fast_first", "game_mode"):
        val = getattr(body, key)
        if val is not None:
            db.set_setting(key, "1" if val else "0")
    if body.wake_names is not None:
        db.set_setting("wake_names", body.wake_names.strip()[:120])
    if body.fish_latency is not None and body.fish_latency in tts.FISH_LATENCIES:
        db.set_setting("fish_latency", body.fish_latency)
    if body.fish_api_key is not None:
        db.set_setting("fish_api_key", body.fish_api_key.strip()[:200])
        tts._FISH_FAIL["until"] = 0.0  # новый ключ — пробуем сразу
    if body.tts_enabled is not None:
        db.set_setting("tts_enabled", "1" if body.tts_enabled else "0")
    if body.avatar_reactions is not None:
        db.set_setting("avatar_reactions", "1" if body.avatar_reactions else "0")
    if body.avatar_idle_life is not None:
        db.set_setting("avatar_idle_life", "1" if body.avatar_idle_life else "0")
    if body.avatar_scale is not None:
        db.set_setting("avatar_scale", f"{max(0.5, min(1.6, body.avatar_scale)):.2f}")
    for key in HOTKEY_DEFAULTS:
        val = (getattr(body, key) or "").strip()
        if val and len(val) <= 40:  # пустое — не трогаем
            db.set_setting(key, val)
    return {"ok": True}


@app.get("/api/ui")
async def ui_state():
    """Всё, что главное окно опрашивает раз в несколько секунд, — одним запросом
    (раньше было 4 запроса: state, config ×2, proactive)."""
    cfg = await get_config()
    return {
        "state": state.snapshot(),
        "config": cfg,
        "focus": db.setting("focus_mode", "0") == "1",
        "proactive": db.setting("proactive_enabled", "1") == "1",
        "pending": len(gateway.PENDING),
        "pomodoro": pomodoro.status(),
        "music": music.player.snapshot(),
        "game": gamemode.status(),  # окно прячется и не слушает «Ая, …», пока идёт игра
    }


# ── Конструктор персоны ───────────────────────────────────────────────
class PersonaIn(BaseModel):
    profile: dict


@app.get("/api/persona")
async def persona_get():
    return persona.public_view()


@app.post("/api/persona")
async def persona_set(body: PersonaIn):
    prof = persona.save(body.profile)
    db.log_event("config", f"persona: {prof['name']} / {prof['role']}")
    return {"ok": True, "profile": prof}


@app.post("/api/persona/preview")
async def persona_preview(body: PersonaIn):
    """Какой системный промпт получится из профиля (без сохранения)."""
    from core.prompting import state_line

    prof = persona.normalize(body.profile)
    return {"prompt": persona.build_prompt(prof, state_line(state.snapshot()))}


@app.post("/api/persona/reset")
async def persona_reset():
    return {"ok": True, "profile": persona.reset()}


class SayIn(BaseModel):
    kind: str  # pat | poke | hello | wake




@app.post("/api/react")
async def react(body: SayIn):
    """Короткая реплика на касание аватара — без LLM, мгновенно (плюс кэш озвучки)."""
    import random

    prof = persona.load()
    reactions = persona.reaction_set(prof)  # реплики под характер из конструктора
    lines = reactions.get(body.kind) or reactions["poke"]
    if body.kind == "hello" and prof.get("greeting"):
        lines = [prof["greeting"], *lines]
    mood = {"pat": "joyful", "poke": "surprise", "hello": "joyful", "wake": "thoughtful"}.get(body.kind, "calm")
    return {"text": random.choice(lines), "mood": mood}


class TaskIn(BaseModel):
    kind: str  # reminder | digest
    minutes: int | None = None
    note: str = ""


@app.get("/api/tasks")
async def tasks_get():
    out = []
    for r in db.tasks_list():
        out.append(
            {
                "id": r[0],
                "created": r[1],
                "due_at": r[2],
                "kind": r[3],
                "args": r[4],
                "status": r[5],
                "result": r[6],
            }
        )
    return {"tasks": out}


@app.post("/api/tasks")
async def tasks_add(body: TaskIn):
    if body.kind == "reminder":
        minutes = max(1, min(24 * 60, int(body.minutes or 10)))
        tid = db.add_task("reminder", {"note": body.note[:200]}, time.time() + minutes * 60)
        return {"ok": True, "id": tid}
    if body.kind == "digest":
        tid = db.add_task("digest", {"note": body.note[:200]}, time.time() + 2)  # почти сразу
        return {"ok": True, "id": tid}
    return {"ok": False, "error": "unknown task kind"}


class TaskCancelIn(BaseModel):
    id: int


class GpuIn(BaseModel):
    minutes: int


@app.get("/api/gpu")
async def gpu_get():
    return {"idle_min": modelmgr.idle_minutes(), "engines": tts.engines_loaded(), "touched": modelmgr.status()}


@app.post("/api/gpu")
async def gpu_set(body: GpuIn):
    db.set_setting(modelmgr.GLOBAL_KEY, str(max(0, min(60, body.minutes))))
    return {"ok": True, "idle_min": modelmgr.idle_minutes()}


@app.post("/api/tasks/cancel")
async def tasks_cancel(body: TaskCancelIn):
    if db.cancel_task(body.id):
        return {"ok": True}
    return {"ok": False, "error": "нет такой задачи"}


# ── Завершение по команде приложения ─────────────────────────────────
@app.post("/api/_shutdown")
async def shutdown(request: Request):
    """Приложение Aya закрывается и просит ядро завершиться (токен знает только оно)."""
    token = os.environ.get("AYA_SHUTDOWN_TOKEN", "")
    if not token or request.headers.get("x-aya-token", "") != token:
        raise HTTPException(status_code=403, detail="нет")

    def _bye():
        time.sleep(0.2)
        for stop in (mcp.REGISTRY.stop_all, bridge_telegram.BRIDGE.stop, bridge_discord.BRIDGE.stop,
                     bridge_tguser.BRIDGE.stop, proactive.stop, diary.stop):
            try:
                stop()
            except Exception:  # noqa: BLE001
                pass
        os._exit(0)

    threading.Thread(target=_bye, daemon=True).start()
    return {"ok": True}


# ── Распознавание речи ────────────────────────────────────────────────
class SttIn(BaseModel):
    model: str | None = None
    device: str | None = None


@app.get("/api/stt")
async def stt_get():
    return stt.status()


@app.post("/api/stt")
async def stt_set(body: SttIn):
    if body.model is not None and body.model in stt.MODELS:
        db.set_setting("stt_model", body.model)
    if body.device is not None and body.device in ("auto", "cuda", "cpu"):
        db.set_setting("stt_device", body.device)
    stt.reset()
    return stt.status()


# ── Свои голоса (конструктор) ─────────────────────────────────────────
@app.get("/api/voices")
async def voices_list():
    return {"custom": tts.custom_voices()}


def _save_custom_voice(data: bytes, label: str, text: str) -> dict:
    """Образец → 24 кГц моно WAV ≤ 12 с; без текста — расшифруем whisper'ом."""
    import re as _re
    import secrets as _secrets
    import tempfile
    import wave as _wave

    import numpy as np
    from faster_whisper.audio import decode_audio

    tmp = os.path.join(tempfile.gettempdir(), f"aya-voice-{_secrets.token_hex(4)}")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        audio = decode_audio(tmp, sampling_rate=24000)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    dur = len(audio) / 24000
    if dur < 3:
        raise ValueError("образец короче 3 секунд — запиши фразу подлиннее")
    trimmed = dur > 15 or not text.strip()
    if dur > 12:
        audio = audio[: 24000 * 12]
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    slug = _re.sub(r"[^a-z0-9]+", "", label.lower().encode("ascii", "ignore").decode())[:16] or "voice"
    vid = f"my_{slug}_{_secrets.token_hex(2)}"
    d = tts.CUSTOM_DIR / vid
    d.mkdir(parents=True, exist_ok=True)
    with _wave.open(str(d / "ref.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm.tobytes())
    if trimmed:
        # текст должен совпадать с образцом слово в слово — расшифровываем обрезанный кусок
        text = stt.transcribe((d / "ref.wav").read_bytes())
    (d / "ref.txt").write_text(text.strip(), "utf-8")
    (d / "meta.json").write_text(json.dumps({"label": label.strip()[:32] or "Мой голос", "seconds": round(min(dur, 12), 1)}, ensure_ascii=False), "utf-8")
    db.log_event("voice", f"свой голос {vid} ({min(dur, 12):.1f} с)")
    return {"id": vid, "label": label.strip()[:32] or "Мой голос", "text": text.strip(), "seconds": round(min(dur, 12), 1)}


@app.post("/api/voices/custom")
async def voices_custom_add(file: UploadFile, label: str = Form("Мой голос"), text: str = Form("")):
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        return {"ok": False, "error": "файл больше 25 МБ"}
    try:
        info = await asyncio.to_thread(_save_custom_voice, data, label, text)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"не удалось обработать звук: {str(e)[:160]}"}
    return {"ok": True, **info}


@app.post("/api/voices/custom/{vid}/delete")
async def voices_custom_delete(vid: str):
    import shutil

    if not tts.is_custom(vid):
        return {"ok": False, "error": "нет такого голоса"}
    shutil.rmtree(tts.CUSTOM_DIR / vid, ignore_errors=True)
    if db.setting("tts_voice_id", "") == vid:
        db.set_setting("tts_voice_id", tts.DEFAULT_VOICE)
    return {"ok": True}


# ── Дневник и лента воспоминаний ──────────────────────────────────────
class DiaryIn(BaseModel):
    day: str = ""


@app.get("/api/diary")
async def diary_list(limit: int = 60):
    return {"entries": diary.entries(max(1, min(365, limit))), "enabled": diary.enabled()}


@app.post("/api/diary/write")
async def diary_write(body: DiaryIn):
    import datetime as _dt

    try:
        day = _dt.date.fromisoformat(body.day) if body.day else _dt.date.today()
    except ValueError:
        return {"ok": False, "error": "дата в формате ГГГГ-ММ-ДД"}
    try:
        entry = await asyncio.to_thread(lambda: asyncio.run(diary.write(day, force=True)))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"модель не ответила: {str(e)[:120]}"}
    if not entry:
        return {"ok": False, "error": "за этот день почти не было разговоров"}
    return {"ok": True, "entry": entry}


@app.post("/api/diary/delete")
async def diary_delete(body: DiaryIn):
    return {"ok": diary.delete(body.day)}


@app.get("/api/memory/search")
async def memory_search(q: str, limit: int = 12):
    return {"results": await asyncio.to_thread(longmem.search, q, limit)}


# ── Помодоро ──────────────────────────────────────────────────────────
class PomodoroIn(BaseModel):
    action: str = "start"  # start | stop
    focus: int = 25
    rest: int = 5
    rounds: int = 4


@app.get("/api/pomodoro")
async def pomodoro_get():
    return pomodoro.status()


@app.post("/api/pomodoro")
async def pomodoro_set(body: PomodoroIn):
    if body.action == "stop":
        return pomodoro.stop()
    return pomodoro.start(body.focus, body.rest, body.rounds)


# ── Музыка ────────────────────────────────────────────────────────────
class MusicConfigIn(BaseModel):
    service: str | None = None  # youtube | yandex
    duck: str | None = None  # lower | pause | off
    youtube_site: str | None = None  # youtube | music
    youtube_radio: bool | None = None
    yandex_target: str | None = None  # aya | app | web
    ym_token: str | None = None


def _music_config() -> dict:
    return {
        "service": music.default_service(),
        "duck": music.duck_mode(),
        "youtube_site": "music" if db.setting("music_youtube_site", "youtube") == "music" else "youtube",
        "youtube_radio": db.setting("music_youtube_radio", "1") != "0",
        "yandex_target": music.yandex.target(),
        "yandex_target_set": db.setting("music_yandex_target", "aya"),
        "ym_token_set": bool(music.yandex.token()),  # сам токен наружу не отдаём
    }


@app.get("/api/music")
async def music_get():
    return {"now": await music.call(music.now_playing), **_music_config()}


@app.post("/api/music/config")
async def music_config(body: MusicConfigIn):
    if body.service in ("youtube", "yandex"):
        db.set_setting("music_service", body.service)
    if body.duck in music.DUCK_MODES:
        db.set_setting("music_duck", body.duck)
    if body.youtube_site in ("youtube", "music"):
        db.set_setting("music_youtube_site", body.youtube_site)
    if body.youtube_radio is not None:
        db.set_setting("music_youtube_radio", "1" if body.youtube_radio else "0")
    if body.yandex_target in ("aya", "app", "web"):
        db.set_setting("music_yandex_target", body.yandex_target)
    if body.ym_token is not None:
        db.set_setting("ym_token", body.ym_token.strip()[:300])
        music.yandex.reset()
    return {"ok": True, **_music_config()}


@app.get("/api/music/yandex/account")
async def music_yandex_account():
    return await asyncio.to_thread(music.yandex.account)


@app.post("/api/music/yandex/login")
async def music_yandex_login():
    """Открыть в браузере вход Яндекса: после входа токен будет в адресной строке."""
    await asyncio.to_thread(music.open_in_browser, music.yandex.TOKEN_HELP_URL)
    return {"ok": True}


class MusicPlayIn(BaseModel):
    query: str = ""
    service: str = ""
    what: str = ""  # likes | wave


@app.post("/api/music/play")
async def music_play(body: MusicPlayIn):
    service = body.service or music.default_service()
    if service == "yandex":
        return await music.call(music.play_yandex, body.query, body.what)
    return await music.call(music.play_youtube, body.query)


class MusicControlIn(BaseModel):
    action: str = "toggle"


@app.post("/api/music/control")
async def music_control(body: MusicControlIn):
    return await music.call(music.control, body.action)


class MusicDuckIn(BaseModel):
    on: bool
    reason: str = "speak"


@app.post("/api/music/duck")
async def music_duck(body: MusicDuckIn):
    """Окно: Ая начала/закончила говорить или слушать — приглушить музыку."""
    return await music.call(music.duck, body.on, body.reason[:20])


@app.get("/api/music/player")
async def music_player_state():
    """Собственный плеер Аи: окно опрашивает, пока что-то играет."""
    return music.player.snapshot()


class MusicPlayerIn(BaseModel):
    cmd: str  # ended | error | pause | play | next | prev | stop | volup | voldown


@app.post("/api/music/player")
async def music_player_cmd(body: MusicPlayerIn):
    return await asyncio.to_thread(music.player.command, body.cmd[:10])


@app.get("/api/music/stream/{item_id}")
async def music_stream(item_id: str, request: Request):
    """Звук трека Яндекса через ядро: прямые ссылки живут минуты и без CORS,
    а окну нужен свой адрес (и для анализа звука). Range пробрасываем — перемотка."""
    import httpx

    if not re.fullmatch(r"[\d:]{1,40}", item_id):
        raise HTTPException(status_code=400, detail="bad id")
    try:
        url = await asyncio.to_thread(music.yandex.stream_url, item_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)[:120])
    headers = {"User-Agent": "Mozilla/5.0"}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=8, read=30, write=8, pool=8), follow_redirects=True, trust_env=False)
    req = client.build_request("GET", url, headers=headers)
    resp = await client.send(req, stream=True)
    if resp.status_code >= 400:
        await resp.aclose()
        await client.aclose()
        music.yandex._URLS.pop(item_id, None)  # ссылка протухла — в следующий раз новая
        raise HTTPException(status_code=502, detail=f"yandex {resp.status_code}")

    async def body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    out = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    for h in ("content-length", "content-range"):
        if resp.headers.get(h):
            out[h.title()] = resp.headers[h]
    return StreamingResponse(body(), status_code=resp.status_code, media_type="audio/mpeg", headers=out)


# ── Мастер первого запуска ────────────────────────────────────────────
def _read_cfg() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError):
        return {}


def _setup_view() -> dict:
    cfg = _read_cfg()
    try:
        step = max(0, min(5, int(db.setting("setup_step", "0") or 0)))
    except ValueError:
        step = 0
    flag = db.setting("setup_done", "")
    if flag == "":
        # решаем один раз: у кого модель уже настроена (автор, старые версии) — мастер не нужен.
        # Потом смотрим только на флаг — иначе ключ, сохранённый на шаге «Модель»,
        # засчитал бы мастер пройденным, и закрытый на середине мастер не вернулся бы.
        flag = "1" if setup.llm_configured(cfg.get("llm")) else "0"
        db.set_setting("setup_done", flag)
    llm = {k: v for k, v in _llm_view(cfg.get("llm")).items() if k != "api_key_tail"}
    return {"done": flag == "1", "step": step, "llm": llm, "fish_key_set": bool(db.setting("fish_api_key", "").strip())}


class SetupIn(BaseModel):
    step: int | None = None
    done: bool | None = None


class SetupLlmIn(BaseModel):
    type: str = "ollama"
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class SetupFishIn(BaseModel):
    key: str = ""


@app.get("/api/setup")
async def setup_get():
    return _setup_view()


@app.post("/api/setup")
async def setup_set(body: SetupIn):
    if body.step is not None:
        db.set_setting("setup_step", str(max(0, min(5, body.step))))
    if body.done is not None:
        db.set_setting("setup_done", "1" if body.done else "0")
    return _setup_view()


@app.post("/api/setup/llm/check")
async def setup_llm_check(body: SetupLlmIn):
    ltype = body.type if body.type in ("ollama", "openai_compatible") else "openai_compatible"
    key = body.api_key.strip()
    if not key:
        saved = _read_cfg().get("llm") or {}
        same = str(saved.get("base_url") or "").rstrip("/") == body.base_url.strip().rstrip("/") and saved.get("type") == ltype
        if same:
            key = str(saved.get("api_key") or "").strip()  # тот же сервис — проверяем сохранённым ключом
        elif not setup.is_local(body.base_url):
            # новый сервис без ключа: старый ключ на чужой адрес не отправляем
            return {"ok": False, "error": "key", "models": []}
    r = await asyncio.to_thread(setup.llm_check, ltype, body.base_url, key, body.model.strip())
    if r["ok"]:
        model = body.model.strip() or (r["models"][0] if r["models"] else "")
        _save_llm_section("llm", body.base_url.strip().rstrip("/"), model, ltype, body.api_key.strip())
        db.log_event("config", f"setup: модель {ltype} {model}")
    return r


@app.post("/api/setup/fish/check")
async def setup_fish_check(body: SetupFishIn):
    r = await asyncio.to_thread(setup.fish_check, body.key)
    if r["ok"]:
        db.set_setting("fish_api_key", body.key.strip()[:200])
        tts._FISH_FAIL["until"] = 0.0
    return r


SETUP_LINKS = {
    "ollama_keys": "https://ollama.com/settings/keys",
    "fish_keys": "https://fish.audio/app/api-keys/",
    "openrouter_keys": "https://openrouter.ai/settings/keys",
}


class SetupOpenIn(BaseModel):
    target: str = ""


@app.post("/api/setup/open")
async def setup_open(body: SetupOpenIn):
    """Открыть страницу ключей в браузере — только из белого списка."""
    url = SETUP_LINKS.get(body.target)
    if not url:
        raise HTTPException(404, "нет такой страницы")
    await asyncio.to_thread(music.open_in_browser, url)
    return {"ok": True}


# ── Докачка по кнопке (мастер и консоль) ──────────────────────────────
@app.get("/api/setup/packs")
async def setup_packs():
    return {"packs": runtime.status(), "whisper": runtime.whisper_status(), "gpu": await asyncio.to_thread(runtime.gpu_present)}


@app.post("/api/setup/packs/{name}")
async def setup_pack_install(name: str):
    if name not in runtime.PACKS:
        raise HTTPException(404, "нет такого набора")
    return runtime.install(name)


@app.post("/api/setup/whisper/prefetch")
async def setup_whisper_prefetch():
    size, _device = stt.wanted()
    runtime.prefetch_whisper(size)
    return runtime.whisper_status()


# ── Рабочий стол (прогулки Аи) ────────────────────────────────────────
@app.get("/api/desktop/foreground")
async def desktop_foreground():
    return {"window": await asyncio.to_thread(desktop.foreground)}


if __name__ == "__main__":
    db.init(DB_PATH)
    log_dir = paths.logs_dir()

    def warm_stt() -> None:
        try:
            stt.get_model()
        except Exception:
            pass

    def warm_tts() -> None:
        try:
            tts.warm()
        except Exception:
            pass

    threading.Thread(target=warm_stt, daemon=True).start()
    threading.Thread(target=warm_tts, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8330, log_level="info", access_log=False)

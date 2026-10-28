import hashlib
import json
import os
import sys
import threading
import traceback
import wave
from pathlib import Path

from .. import db
from . import modelmgr, textnorm

_cache_dir = Path.home() / ".aya" / "tts-cache"
_cache_dir.mkdir(parents=True, exist_ok=True)

_VITS_DIR = Path(__file__).parent / "dialogs_vits2"
_F5_DIR = Path(__file__).parent / "f5_ru"


def _f5_ckpt_dir() -> Path:
    """Веса F5: скачанные по кнопке (~/.aya/voices-local/f5), иначе рядом с кодом (разработка)."""
    from ..runtime import f5_dir

    got = f5_dir()
    if (got / "model_ru_v2_inference.safetensors").is_file() and (got / "vocab_ru.txt").is_file():
        return got
    return _F5_DIR / "ckpt"

DEFAULT_EDGE_VOICE = "ru-RU-SvetlanaNeural"
# голос по умолчанию в релизе — облачный Fish; без ключа ядро само говорит через Edge
DEFAULT_VOICE = "fish_miyuki"

# mood -> emotion_id of Dialogs-RU model
EMOTION_MAP = {
    "calm": 0,        # neutral
    "joyful": 1,      # happy
    "surprise": 2,
    "thoughtful": 7,  # whisper
    "angry": 9,
    "sad": 10,
    "laugh": 6,
}

# mood -> F5 speed
F5_SPEED = {"joyful": 0.97, "calm": 1.0, "thoughtful": 1.05, "sad": 0.95}

_lock = threading.RLock()
_vits_engine = None
_f5_engine = None
_refs = {}
_accent_cache: dict[str, str] = {}


def _trim_edges(pcm, sr: int):
    import numpy as np

    x = pcm.astype(np.float32)
    if x.size < sr // 4:
        return pcm
    thr = float(max(120.0, 0.008 * float(np.abs(x).max() or 0.0)))
    idx = np.abs(x) > thr
    if not idx.any():
        return pcm
    head = int(np.argmax(idx))
    tail = int(x.size - np.argmax(idx[::-1]))
    pad = int(sr * 0.18)
    a = max(0, head - pad)
    b = min(x.size, tail + pad)
    if b - a < sr // 4 or (a <= 0 and b >= x.size):
        return pcm
    return pcm[a:b]


def _accentize(text: str) -> str:
    """Place + stress marks via RUAccent (reuses VITS engine's accentizer)."""
    if text in _accent_cache:
        return _accent_cache[text]
    try:
        eng = _get_vits()
        acc = getattr(eng, "accentizer", None)
        out = acc.process_all(text) if acc else text
    except Exception:
        out = text
    if len(_accent_cache) > 500:
        _accent_cache.clear()
    _accent_cache[text] = out
    return out


def _get_vits():
    global _vits_engine
    with _lock:
        if _vits_engine is None:
            sys.path.insert(0, str(_VITS_DIR))
            from tts import DialogsTTS

            _vits_engine = DialogsTTS()
        modelmgr.touch("vits")
        return _vits_engine


def _create_f5_engine():
    import glob as _g

    import soundfile as _sf
    import torchaudio as _ta

    def _sf_load(path, *a, **k):
        data, sr = _sf.read(path, dtype="float32", always_2d=True)
        return __import__("torch").from_numpy(data.T), sr

    _ta.load = _sf_load
    _ta.functional.load = _sf_load
    ckpt_dir = _f5_ckpt_dir()
    if not _g.glob(str(ckpt_dir / "*.safetensors")):
        raise RuntimeError("no f5 checkpoint")
    from f5_tts.api import F5TTS

    ckpt = str(ckpt_dir / "model_ru_v2_inference.safetensors")
    vocab = str(ckpt_dir / "vocab_ru.txt")
    engine = F5TTS(
        model="F5TTS_v1_Base",
        ckpt_file=ckpt,
        vocab_file=vocab,
    )
    try:
        if db.setting("tts_vocoder", "vocos") != "bigvgan":
            return engine
        bg_dir = _F5_DIR.parent / "third_party" / "BigVGAN"
        if not (bg_dir / "bigvgan.py").exists():
            bg_dir = Path(
                str(Path(sys.executable).parent.parent)
                + "/Lib/site-packages/third_party/BigVGAN"
            )
        if (bg_dir / "bigvgan.py").exists():
            sys.path.insert(0, str(bg_dir))
            import bigvgan as _bg

            v = _bg.BigVGAN.from_pretrained(
                "nvidia/bigvgan_v2_24khz_100band_256x",
                use_cuda_kernel=False,
                cache_dir=str(_F5_DIR / "bg_cache"),
            )
            v.remove_weight_norm()
            v = v.eval().to(engine.device)
            engine.vocoder = v
            engine.mel_spec_type = "bigvgan"
    except Exception:
        traceback.print_exc()
    return engine


def _get_f5():
    global _f5_engine
    with _lock:
        if _f5_engine is None:
            _f5_engine = _create_f5_engine()
        modelmgr.touch("f5")
        return _f5_engine


def _speakers_cfg() -> tuple[int, float]:
    sp = db.setting("tts_speaker", "1")  # 0 Masha F, 1 Sveta F, 2 Dima M
    try:
        sid = int(sp)
    except ValueError:
        sid = 0
    if sid not in (0, 1, 2):
        sid = 0
    ls = db.setting("tts_length_scale", "1.12")
    try:
        scale = float(ls)
    except ValueError:
        scale = 1.12
    return sid, scale


REF_TEXT = "Ага, я поняла! Пойдём посмотрим вместе, хорошо? Мне кажется, это будет по-настоящему интересно."

REF_EMOTION = {"calm": 1, "joyful": 1, "thoughtful": 7}


def _polish(audio: "np.ndarray", sr: int) -> "np.ndarray":
    """Cute anime-ish touch: +1.2 semitones + slight brightening."""
    import librosa
    import numpy as np
    import scipy.signal as sig

    y = audio.astype(np.float32) / 32767.0
    y = librosa.effects.pitch_shift(y, sr=sr, n_steps=1.2)
    sos_bright = sig.butter(2, 5000, "highpass", fs=sr, output="sos")
    bright = sig.sosfilt(sos_bright, y)
    sos_low = sig.butter(2, 160, "highpass", fs=sr, output="sos")
    out = y * 0.92 + bright * 0.18
    out = sig.sosfilt(sos_low, out) * 1.04
    peak = float(np.abs(out).max())
    if peak > 0:
        out = out / peak * 0.95
    return (out * 32767).astype(audio.dtype)


def _make_ref(mood: str) -> tuple[Path, str]:
    """Render Sveta VITS reference clip for F5 cloning (per mood style)."""
    cute = db.setting("tts_cute_ref", "1") == "1"
    suffix = "_cute" if cute else ""
    path = _F5_DIR / f"ref_{mood}{suffix}.wav"
    if path.exists() and path.stat().st_size > 1000:
        return path, REF_TEXT
    emo = REF_EMOTION.get(mood, EMOTION_MAP.get(mood, 0))
    engine = _get_vits()
    sid, scale = _speakers_cfg()
    with _lock:
        sr, audio, _ = engine.synthesize(
            REF_TEXT, sid, emo, True, True, 0.6, 0.8, max(scale, 1.15)
        )
    if cute:
        audio = _polish(audio, sr)
    tmp = str(path) + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(audio.tobytes())
    os.replace(tmp, str(path))
    return path, REF_TEXT


def _get_ref(mood: str) -> tuple[str, str]:
    m = mood if mood in EMOTION_MAP else "calm"
    with _lock:
        if m not in _refs:
            _refs[m] = tuple(map(str, _make_ref(m)))
        return _refs[m]


# real human reference voices (anime-ish picks) — audio files + auto transcripts
REAL_REFS = {
    "f1": "refs_real/female_1.wav",
    "f2": "refs_real/female_2.wav",
    "f3": "refs_real/female_3.wav",
    "f4": "refs_real/female_4.wav",
    "v1": "refs_real/voice_female_1.wav",
    "v2": "refs_real/voice_female_2.wav",
}
VITS_VOICES = {"vits_masha": 0, "vits_sveta": 1, "vits_dima": 2}

# Qwen3-TTS voice-clone voices (reference refs_real/female_3.wav + shared transcript)
QWEN_REFS = {
    "qwen_06": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    # своя копия рядом с кодом (разработка), иначе — с Hugging Face
    "qwen_17": str(_F5_DIR.parent / "qwen" / "1.7B-Base") if (_F5_DIR.parent / "qwen" / "1.7B-Base").is_dir() else "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
}

# Fish Audio cloud voices (anime, Russian) — reference ids on api.fish.audio
FISH_VOICES = {
    "fish_miyuki": "df3144db39a34a02bdeeb2d3ad172211",
    "fish_hoshino": "3c13256c933f4d9ab844709e0208e7f7",
    "fish_rei": "931f4d533d0e42379eb05c08e319c811",
    "fish_anime": "f3952cdc0a314cb2836bc448071204a3",
    "fish_playful": "832bfb4965164858b443ea6ddcce715d",
    "fish_mugi": "0871828f07334aab87354d254dbd508a",
}

_qwen_engine = None
_qwen_key = ""
_trans_path = _F5_DIR / "refs_real" / "transcripts.json"


CUSTOM_DIR = Path.home() / ".aya" / "voices"


def is_custom(vid: str) -> bool:
    return vid.startswith("my_") and (CUSTOM_DIR / vid / "ref.wav").is_file()


def is_f5_ref(vid: str) -> bool:
    """Голос F5 по образцу: встроенный (REAL_REFS) или свой из конструктора."""
    return vid in REAL_REFS or is_custom(vid)


def custom_voices() -> list[dict]:
    out = []
    if CUSTOM_DIR.is_dir():
        for d in sorted(CUSTOM_DIR.iterdir()):
            if d.is_dir() and is_custom(d.name):
                try:
                    meta = json.loads((d / "meta.json").read_text("utf-8"))
                except (OSError, ValueError):
                    meta = {}
                out.append({"id": d.name, "label": meta.get("label") or d.name[3:], "sub": "твой голос · F5", "custom": True})
    return out


def _rel_ref(path: str) -> str:
    """Ключ текста образца — путь относительно f5_ru («refs_real/female_1.wav»):
    в установленном приложении ядро лежит не там, где у разработчика."""
    try:
        return Path(path).resolve().relative_to(_F5_DIR.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _user_trans_path() -> Path:
    from .. import paths

    return paths.voices_local_dir() / "transcripts.json"


def _load_json(p: Path) -> dict:
    import json

    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _transcript_get(rel: str) -> str:
    for src in (_user_trans_path(), _trans_path):
        data = _load_json(src)
        if rel in data:
            return data[rel]
        for k, v in data.items():  # старые ключи — абсолютные пути разработчика
            if k.replace("\\", "/").endswith("/" + rel):
                return v
    return ""


def _transcript_put(rel: str, txt: str) -> None:
    """Новые тексты — в ~/.aya (папку установки обновление заменяет целиком)."""
    import json

    p = _user_trans_path()
    data = _load_json(p)
    data[rel] = txt
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    except OSError:
        pass


def _ref_text(path: str) -> str:
    rel = _rel_ref(path)
    txt = _transcript_get(rel)
    if not txt:
        from f5_tts.infer.utils_infer import preprocess_ref_audio_text

        _pf, txt = preprocess_ref_audio_text(path, "", show_info=lambda *a, **k: None)
        _transcript_put(rel, txt)
    return txt


def _real_ref(vid: str) -> tuple[str, str]:
    if is_custom(vid):
        # свой голос: образец и его текст лежат в ~/.aya/voices/<id>/
        d = CUSTOM_DIR / vid
        txt = (d / "ref.txt").read_text("utf-8").strip() if (d / "ref.txt").exists() else ""
        return str(d / "ref.wav"), txt
    path = str(_F5_DIR / REAL_REFS[vid])
    with _lock:
        if vid not in _refs:
            _refs[vid] = _ref_text(path)
        return path, _refs[vid]


def _qwen_ref_text() -> str:
    ref_path = str(_F5_DIR / "refs_real" / "female_3.wav")  # образец для клона Qwen
    return ref_path, _ref_text(ref_path)


def _get_qwen(size: str):
    global _qwen_engine, _qwen_key
    with _lock:
        if _qwen_engine is not None and _qwen_key != size:
            _qwen_engine = None
            try:
                import gc

                import torch

                gc.collect()
                torch.cuda.empty_cache()
            except Exception:
                pass
        if _qwen_engine is None:
            import torch

            from qwen_tts import Qwen3TTSModel

            _qwen_engine = Qwen3TTSModel.from_pretrained(
                QWEN_REFS[size], device_map="cuda:0", dtype=torch.bfloat16
            )
            ref_path, ref_text = _qwen_ref_text()
            _qwen_engine._aya_prompt = _qwen_engine.create_voice_clone_prompt(
                ref_audio=ref_path, ref_text=ref_text
            )
            _qwen_key = size
        modelmgr.touch("qwen")
    return _qwen_engine


WARM_PHRASES = 6


def frequent_phrases(limit: int = WARM_PHRASES) -> list[str]:
    """Самые частые короткие реплики Аи из истории — их выгодно держать в TTS-кэше."""
    rows = db.query(
        "SELECT text FROM messages WHERE role='assistant' AND length(text) BETWEEN 2 AND 60 "
        "GROUP BY text ORDER BY COUNT(*) DESC, MAX(id) DESC LIMIT ?",
        (limit,),
    )
    return [r[0].strip() for r in rows if r[0] and r[0].strip()]


def warm_phrases(limit: int = WARM_PHRASES) -> None:
    """Греет кэш fish-голоса частыми репликами: они зазвучат мгновенно.

    Только fish и только при наличии ключа: fallback на qwen/f5 здесь не нужен —
    не хотим на старте тянуть тяжёлые модели. Первая ошибка — стоп, не долбим сеть.
    """
    vid = db.setting("tts_voice_id", DEFAULT_VOICE)
    if vid not in FISH_VOICES or not db.setting("fish_api_key", "").strip():
        return
    import asyncio

    for text in frequent_phrases(limit):
        try:
            if asyncio.run(_fish_speak(text, vid)) is None:
                return
        except Exception:
            return


def warm() -> None:
    """Греет только движок, нужный выбранному голосу.

    fish — облачный голос: локальные VITS/F5/Qwen не нужны, fallback speak()
    загрузит по требованию. Для локального голоса греем только его движок,
    чтобы не держать в памяти лишние гигабайты.
    """
    sel = db.setting("tts_voice_id", DEFAULT_VOICE)
    with _lock:
        if sel in FISH_VOICES:
            pass
        elif sel in VITS_VOICES:
            try:
                _get_vits()
            except Exception:
                pass
        elif sel in QWEN_REFS:
            try:
                _get_qwen(sel)
            except Exception:
                pass
        else:
            try:
                _get_f5()
                _get_ref("calm")
                _get_ref("joyful")
                _get_ref("thoughtful")
                for vid in REAL_REFS:
                    _real_ref(vid)
            except Exception:
                pass
    modelmgr.start()
    warm_phrases()


def _unload_qwen() -> bool:
    global _qwen_engine, _qwen_key
    with _lock:
        if _qwen_engine is None:
            return False
        _qwen_engine = None
        _qwen_key = ""
        return True


def _unload_f5() -> bool:
    global _f5_engine
    with _lock:
        if _f5_engine is None:
            return False
        _f5_engine = None
        return True


def _unload_vits() -> bool:
    global _vits_engine
    with _lock:
        if _vits_engine is None:
            return False
        _vits_engine = None
        return True


def engines_loaded() -> dict[str, bool]:
    return {
        "vits": _vits_engine is not None,
        "f5": _f5_engine is not None,
        "qwen": _qwen_engine is not None,
    }


modelmgr.register("vits", _unload_vits)
modelmgr.register("f5", _unload_f5)
modelmgr.register("qwen", _unload_qwen)


def _edge_speak(text: str) -> tuple[str, int]:
    import edge_tts as _e

    voice = db.setting("tts_voice", DEFAULT_EDGE_VOICE)
    text = text.strip()[:600]
    h = hashlib.sha1(f"edge|{voice}|{text}".encode()).hexdigest()
    path = str(_cache_dir / f"{h}.mp3")
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        tmp = path + ".part"
        # save() в edge-tts асинхронный: без await файл не писался и запасной
        # голос всегда падал. Функция зовётся из потока — берём save_sync.
        _e.Communicate(text, voice, rate="-5%", pitch="+2Hz").save_sync(tmp)
        os.replace(tmp, path)
    return path, 0


async def _f5_speak(text: str, mood: str, nfe_override: int | None = None) -> tuple[str, int] | None:
    import asyncio

    try:
        # загрузка моделей — секунды/десятки секунд: только в потоке,
        # иначе замирает всё ядро (чат, стрим, опрос окна)
        engine = await asyncio.to_thread(_get_f5)
        ref_file, ref_text = await asyncio.to_thread(_get_ref, mood)
    except Exception:
        return None
    speed = F5_SPEED.get(mood, 1.0)
    nfe = nfe_override or int(db.setting("tts_nfe", "32"))
    cfg = float(db.setting("tts_cfg_cute", "1.4"))
    acc = await asyncio.to_thread(_accentize, text)
    if len(acc) > 600:
        acc = acc[:600]
    h = hashlib.sha1(f"f5|{mood}|{speed}|{cfg}|{nfe}|t2|{acc}".encode()).hexdigest()
    path = str(_cache_dir / f"{h}.wav")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, 1
    import asyncio

    try:
        wav, sr, _ = await asyncio.to_thread(
            engine.infer,
            ref_file,
            ref_text,
            acc,
            lambda *a, **k: None,
            None,
            nfe_step=nfe,
            speed=speed,
            remove_silence=False,
            cfg_strength=cfg,
        )
    except TypeError:
        wav, sr, _ = await asyncio.to_thread(engine.infer, ref_file, ref_text, text, nfe_step=24, speed=speed)
    import numpy as np

    pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16)
    pcm = _trim_edges(pcm, int(sr))
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)
    return path, 0


async def _vits_speak(text: str, mood: str, sid_override: int | None = None) -> tuple[str, int] | None:
    import asyncio

    try:
        engine = await asyncio.to_thread(_get_vits)
        sid_cfg, scale = _speakers_cfg()
        sid = int(sid_override) if sid_override is not None else sid_cfg
    except Exception:
        return None
    emo = EMOTION_MAP.get(mood, 0)
    h = hashlib.sha1(f"vits|{sid}|{emo}|{scale:.2f}|{text}".encode()).hexdigest()
    path = str(_cache_dir / f"{h}.wav")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, 1
    import asyncio

    try:
        with _lock:
            sr, audio, _used = await asyncio.to_thread(
                engine.synthesize, text, sid, emo, True, True, 0.6, 0.8, scale
            )
    except Exception:
        return None
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(audio.tobytes())
    os.replace(tmp, path)
    return path, 0


async def _f5_real(text: str, vid: str, mood: str, nfe_override: int | None = None) -> tuple[str, int] | None:
    import asyncio

    try:
        engine = await asyncio.to_thread(_get_f5)
        ref_file, ref_text = await asyncio.to_thread(_real_ref, vid)
    except Exception:
        return None
    nfe = nfe_override or int(db.setting("tts_nfe", "32"))
    cfg = float(db.setting("tts_cfg", "2.0"))
    acc = await asyncio.to_thread(_accentize, text)
    if len(acc) > 600:
        acc = acc[:600]
    h = hashlib.sha1(f"f5r|{vid}|{cfg}|{nfe}|t2|{acc}".encode()).hexdigest()
    path = str(_cache_dir / f"{h}.wav")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, 1
    import asyncio

    try:
        wav, sr, _ = await asyncio.to_thread(
            engine.infer,
            ref_file,
            ref_text,
            acc,
            lambda *a, **k: None,
            None,
            nfe_step=nfe,
            speed=1.0,
            remove_silence=False,
            cfg_strength=cfg,
        )
    except Exception:
        traceback.print_exc()
        return None
    import numpy as np

    pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16)
    pcm = _trim_edges(pcm, int(sr))
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)
    return path, 0


async def _qwen_speak(text: str, vid: str) -> tuple[str, int] | None:
    import asyncio

    try:
        engine = await asyncio.to_thread(_get_qwen, vid)
    except Exception:
        traceback.print_exc()
        return None
    h = hashlib.sha1(f"qwen|{vid}|t2|{text}".encode()).hexdigest()
    path = str(_cache_dir / f"{h}.wav")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, 1
    import asyncio

    import numpy as np

    try:
        wavs, sr = await asyncio.to_thread(
            engine.generate_voice_clone,
            text=text,
            language="Russian",
            voice_clone_prompt=engine._aya_prompt,
            temperature=0.8,
            top_k=40,
            top_p=0.87,
        )
    except Exception:
        traceback.print_exc()
        return None
    pcm = (np.clip(wavs[0], -1, 1) * 32767).astype(np.int16)
    if len(pcm) > sr * 60:
        pcm = pcm[: sr * 60]
    pcm = _trim_edges(pcm, int(sr))
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)
    return path, 0


FISH_URL = "https://api.fish.audio/v1/tts"
FISH_MODEL = "s2.1-pro-free"
# low — самый быстрый старт, balanced — почти так же быстро и стабильнее, normal — лучшее качество
FISH_LATENCIES = ("low", "balanced", "normal")
_FISH_FAIL = {"until": 0.0}
_FISH_CLIENT: dict = {"client": None, "loop": None}


def fish_latency() -> str:
    v = db.setting("fish_latency", "balanced").strip().lower()
    return v if v in FISH_LATENCIES else "balanced"


def is_cloud(vid: str = "") -> bool:
    return (vid or db.setting("tts_voice_id", DEFAULT_VOICE)) in FISH_VOICES


def fish_ready() -> bool:
    """Ключ есть и Fish недавно не падал (после сбоя пару секунд не ждём его зря)."""
    import time as _t

    return bool(db.setting("fish_api_key", "").strip()) and _t.time() >= _FISH_FAIL["until"]


def _fish_failed(status: int) -> None:
    import time as _t

    # нет ключа / кончились кредиты — это надолго; сеть или очередь — ненадолго
    _FISH_FAIL["until"] = _t.time() + (300 if status in (401, 402, 403) else 20)


class _FishClient:
    """Одно постоянное соединение с Fish (TLS-рукопожатие не на каждую фразу).

    httpx.AsyncClient привязан к своему event loop. Основной цикл ядра держит
    общий клиент; вызовы из других циклов (прогрев фраз в потоке через
    asyncio.run) получают временный клиент, который закрывается после запроса.
    """

    def __init__(self):
        self.temp = None

    async def __aenter__(self):
        import asyncio

        import httpx

        loop = asyncio.get_running_loop()
        cur, owner = _FISH_CLIENT["client"], _FISH_CLIENT["loop"]
        if cur is not None and owner is loop:
            return cur
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=4, keepalive_expiry=120),
            trust_env=False,
        )
        if cur is None or owner is None or owner.is_closed():
            _FISH_CLIENT["client"], _FISH_CLIENT["loop"] = client, loop
            return client
        self.temp = client
        return client

    async def __aexit__(self, *exc):
        if self.temp is not None:
            await self.temp.aclose()


async def fish_warm_connection() -> None:
    """Открыть соединение заранее, пока модель пишет ответ: первая фраза не ждёт TLS."""
    if not is_cloud() or not fish_ready():
        return
    try:
        async with _FishClient() as c:
            await c.get("https://api.fish.audio/", timeout=5.0)
    except Exception:  # noqa: BLE001 — это только прогрев
        pass


FISH_SR = 24000
# Сырой PCM, а не mp3: окно играет его через WebAudio с первого куска.
# <audio> с mp3-потоком ждал ещё ~1 с, пока разберёт начало файла.
FISH_MEDIA = f"audio/pcm;rate={FISH_SR}"


def _fish_body(text: str, vid: str) -> dict:
    return {
        "text": text,
        "reference_id": FISH_VOICES[vid],
        "format": "pcm",
        "sample_rate": FISH_SR,
        "latency": fish_latency(),
        "chunk_length": 100,
    }


def _write_wav(path: str, pcm: bytes, sr: int) -> None:
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm[: len(pcm) // 2 * 2])
    os.replace(tmp, path)


def _fish_headers() -> dict:
    return {
        "Authorization": "Bearer " + db.setting("fish_api_key", ""),
        "Content-Type": "application/json",
        "model": FISH_MODEL,
    }


async def _fish_speak(text: str, vid: str, on_chunk=None) -> tuple[str, int] | None:
    """Синтез в Fish потоком. on_chunk(bytes) получает PCM по мере прихода."""
    import asyncio

    old = str(_cache_dir / (hashlib.sha1(f"fish|{vid}|t1|{text}".encode()).hexdigest() + ".mp3"))
    path = str(_cache_dir / (hashlib.sha1(f"fish|{vid}|t2|{text}".encode()).hexdigest() + ".wav"))
    for cached in (path, old):  # старый mp3-кэш тоже годится
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            return cached, 1
    if not fish_ready():
        return None
    buf = bytearray()
    try:
        async with _FishClient() as c:
            async with c.stream("POST", FISH_URL, json=_fish_body(text, vid), headers=_fish_headers()) as r:
                if r.status_code != 200:
                    await r.aread()
                    print(f"[tts] fish HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
                    _fish_failed(r.status_code)
                    return None
                async for b in r.aiter_bytes():
                    if b:
                        buf += b
                        if on_chunk is not None:
                            await on_chunk(b)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — сеть, таймаут: отдаём запасному голосу
        traceback.print_exc()
        _fish_failed(0)
        return None
    if len(buf) < 2:
        return None
    _write_wav(path, bytes(buf), FISH_SR)
    return path, 0


async def _cloud_fallback(text: str) -> tuple[str, int] | None:
    """Fish не ответил: сначала быстрый edge-tts, потом локальный Qwen."""
    import asyncio

    try:
        p = await asyncio.to_thread(_edge_speak, text)
        if p and p[0]:
            return p[0], 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return await _qwen_speak(text, "qwen_17")


async def speak_live(text: str, mood: str = "calm", vid_override: str = ""):
    """Для облачного голоса — LiveAudio, звук идёт по мере синтеза.

    None — голос не облачный или Fish недоступен: зовите обычный speak().
    """
    import asyncio

    from .live_audio import LiveAudio

    vid = vid_override or db.setting("tts_voice_id", DEFAULT_VOICE)
    if vid not in FISH_VOICES:
        return None
    text = textnorm.speakable(text.strip()[:600])[:600]
    if not text:
        return None
    live = LiveAudio(FISH_MEDIA)
    key = _index_key(vid, mood, text)
    hit = _index_get(key)
    if hit:
        await live.finish(hit)
        return live

    async def produce():
        path = None
        try:
            r = await _fish_speak(text, vid, on_chunk=live.push)
            if r and r[0]:
                path = r[0]
            elif not live.chunks:
                r = await _cloud_fallback(text)
                path = r[0] if r else None
            if path:
                _index_put(key, path)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            await asyncio.shield(live.finish(path))

    live.task = asyncio.create_task(produce())
    return live


def fast_nfe() -> int | None:
    """Шаги F5 для «быстрой первой фразы»: вдвое меньше — голос стартует раньше."""
    if db.setting("tts_fast_first", "1") == "0":
        return None
    try:
        return max(8, min(32, int(db.setting("tts_nfe_fast", "16"))))
    except ValueError:
        return 16


async def speak(text: str, mood: str = "calm", vid_override: str = "", fast: bool = False) -> tuple[str, int]:
    text = text.strip()[:600]
    if not text:
        return None, 1
    text = textnorm.speakable(text)[:600]
    import time as _time

    t0 = _time.perf_counter()
    vid = vid_override or db.setting("tts_voice_id", DEFAULT_VOICE)
    # быстрый путь: та же фраза тем же голосом уже озвучена — отдаём файл,
    # не загружая ни TTS, ни ударения (раньше кэш проверялся после загрузки движка)
    key = _index_key(vid, mood, text)
    hit = _index_get(key)
    nfe = fast_nfe() if fast else None
    if not hit and nfe:
        # готовой полной версии нет — может, есть быстрая
        key = _index_key(vid + "#fast", mood, text)
        hit = _index_get(key)
    if hit:
        from ..llm import metrics as _svc

        _svc.tts_record(vid, True, (_time.perf_counter() - t0) * 1000)
        return hit, 1
    r = None
    if vid in FISH_VOICES:
        r = await _fish_speak(text, vid)
        if r is None:
            r = await _cloud_fallback(text)
    if r is None and is_f5_ref(vid):
        r = await _f5_real(text, vid, mood, nfe)
        if r is None:
            r = await _f5_speak(text, mood, nfe)
    elif r is None and vid == "cute":
        r = await _f5_speak(text, mood, nfe)
    elif r is None and vid in QWEN_REFS:
        r = await _qwen_speak(text, vid)
    elif r is None and vid in VITS_VOICES:
        r = await _vits_speak(text, mood, VITS_VOICES[vid])
    if r is None and not is_f5_ref(vid):
        r = await _f5_real(text, "f3", mood, nfe)
    if r is None:
        r = await _vits_speak(text, mood, 1)
    if r is None:
        import asyncio

        r = await asyncio.to_thread(_edge_speak, text)
        r = r[0], 0
    from ..llm import metrics as _svc

    if r and r[0]:
        _index_put(key, r[0])
    _svc.tts_record(vid, bool(r and r[1]), (_time.perf_counter() - t0) * 1000)
    return r


_INDEX_FILE = _cache_dir / "index.json"
_index: dict[str, str] | None = None
_index_lock = threading.Lock()


def _index_key(vid: str, mood: str, text: str) -> str:
    return hashlib.sha1(f"idx|{vid}|{mood}|{text}".encode()).hexdigest()


def _index_load() -> dict[str, str]:
    global _index
    if _index is None:
        try:
            _index = json.loads(_INDEX_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            _index = {}
    return _index


def _index_get(key: str) -> str | None:
    with _index_lock:
        path = _index_load().get(key)
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    return None


def _index_put(key: str, path: str) -> None:
    with _index_lock:
        idx = _index_load()
        idx[key] = path
        if len(idx) > 5000:  # держим индекс компактным
            for k in list(idx)[: len(idx) - 4000]:
                idx.pop(k, None)
        try:
            tmp = _INDEX_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(idx, ensure_ascii=False), "utf-8")
            os.replace(tmp, _INDEX_FILE)
        except OSError:
            pass

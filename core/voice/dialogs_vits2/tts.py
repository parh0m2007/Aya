"""Inference engine for the Dialogs-RU expressive VITS2 model.

This module is intentionally free of any UI dependency so it can be imported
and tested on its own. ``app.py`` builds the Gradio interface on top of it.

The model is a multi-band iSTFT VITS2 conditioned on a speaker id and an
emotion id. Russian text is used as-is (Cyrillic, lowercased); an optional
``+`` marks the stressed vowel, which the model was trained with. Stress is
placed automatically with ``ruaccent`` when available.
"""

import json
import os
import re

import numpy as np
import torch

import commons
from models import SynthesizerTrn
from text import text_to_sequence
from text.symbols import symbols

# Russian TTS text normalizer (numbers, dates, money, units, abbreviations…).
# Vendored single file: github.com/shigabeev/russian_tts_normalization (MIT).
try:
    from text.rutextnorm import normalize_russian
except Exception:  # pragma: no cover - fall back to the installed package
    try:
        from rutextnorm import normalize_russian
    except Exception:
        normalize_russian = None

DEVICE = "cpu"
MODEL_REPO = os.environ.get("DIALOGS_MODEL_REPO", "frappuccino/dialogs-ru-vits2")
WEIGHTS_FILE = "averaged_G_615000.pth"

# --- speaker / emotion catalogues -------------------------------------------
# speaker id -> (English label, Russian label, gender)
SPEAKERS = {
    0: ("Masha", "Маша", "female"),
    1: ("Sveta", "Света", "female"),
    2: ("Dima", "Дима", "male"),
}

# emotion id -> (English label, Russian label)  (training order, 13 styles)
EMOTIONS = {
    0: ("neutral", "нейтральный"),
    1: ("happy", "радостный"),
    2: ("surprise", "удивление"),
    3: ("arrogance", "высокомерие"),
    4: ("yawn", "зевота"),
    5: ("fear", "страх"),
    6: ("laughing", "смех"),
    7: ("whisper", "шёпот"),
    8: ("disgust", "отвращение"),
    9: ("angry", "злость"),
    10: ("sad", "грусть"),
    11: ("tongue-twister", "скороговорка"),
    12: ("poem", "стихи"),
}


def _resolve_assets():
    """Return (config_path, weights_path).

    Resolution order:
      1. ``DIALOGS_LOCAL_CKPT`` env var (a folder with config.json + weights),
      2. files sitting next to this module (model-repo clone),
      3. download from the Hugging Face Hub.
    """
    local = os.environ.get("DIALOGS_LOCAL_CKPT")
    if local:
        return os.path.join(local, "config.json"), os.path.join(local, WEIGHTS_FILE)

    here = os.path.dirname(os.path.abspath(__file__))
    cfg_local = os.path.join(here, "config.json")
    wts_local = os.path.join(here, WEIGHTS_FILE)
    if os.path.isfile(cfg_local) and os.path.isfile(wts_local):
        return cfg_local, wts_local

    from huggingface_hub import hf_hub_download

    cfg = hf_hub_download(MODEL_REPO, "config.json")
    wts = hf_hub_download(MODEL_REPO, WEIGHTS_FILE)
    return cfg, wts


def _load_checkpoint(path, model):
    """Load generator weights, tolerating missing/extra keys."""
    ckpt = torch.load(path, map_location="cpu")
    saved = ckpt["model"] if "model" in ckpt else ckpt
    state = model.state_dict()
    new_state = {}
    for k in state:
        new_state[k] = saved[k] if k in saved else state[k]
    model.load_state_dict(new_state)
    return ckpt.get("iteration")


class DialogsTTS:
    def __init__(self):
        config_path, weights_path = _resolve_assets()
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.cfg = cfg
        self.sampling_rate = int(cfg["data"]["sampling_rate"])
        self.add_blank = bool(cfg["data"].get("add_blank", True))
        self.cleaners = cfg["data"].get("text_cleaners", ["basic_cleaners"])

        net_g = SynthesizerTrn(
            len(symbols),
            80,  # mel posterior channels (VITS2)
            cfg["train"]["segment_size"] // cfg["data"]["hop_length"],
            n_speakers=cfg["data"]["n_speakers"],
            n_emotions=cfg["data"]["n_emotions"],
            **cfg["model"],
        ).to(DEVICE)
        net_g.eval()
        self.iteration = _load_checkpoint(weights_path, net_g)
        self.net_g = net_g

        # Optional automatic stress placement.
        self.accentizer = None
        try:
            from ruaccent import RUAccent

            acc = RUAccent()
            acc.load(omograph_model_size="tiny", use_dictionary=True)
            self.accentizer = acc
        except Exception as e:  # pragma: no cover - best effort
            print("ruaccent unavailable, stress will not be auto-placed:", repr(e))

    # -- text helpers --------------------------------------------------------
    def normalize_text(self, text):
        if normalize_russian is None:
            return text
        try:
            return normalize_russian(text)
        except Exception as e:  # pragma: no cover
            print("rutextnorm failed on input:", repr(e))
            return text

    def add_stress(self, text):
        if self.accentizer is None:
            return text
        try:
            return self.accentizer.process_all(text)
        except Exception as e:  # pragma: no cover
            print("ruaccent failed on input:", repr(e))
            return text

    def _to_tokens(self, text):
        seq = text_to_sequence(text, self.cleaners)
        if self.add_blank:
            seq = commons.intersperse(seq, 0)
        return torch.LongTensor(seq)

    # -- synthesis -----------------------------------------------------------
    @torch.no_grad()
    def synthesize(
        self,
        text,
        speaker_id=0,
        emotion_id=0,
        auto_stress=True,
        normalize=True,
        noise_scale=0.667,
        noise_scale_w=0.8,
        length_scale=1.0,
    ):
        if not text or not text.strip():
            raise ValueError("Empty text")
        used = self.normalize_text(text) if normalize else text
        used = self.add_stress(used) if auto_stress else used
        tokens = self._to_tokens(used)
        if tokens.numel() == 0:
            raise ValueError("No recognizable characters in the input text")
        x = tokens.unsqueeze(0).to(DEVICE)
        x_len = torch.LongTensor([tokens.size(0)]).to(DEVICE)
        sid = torch.LongTensor([int(speaker_id)]).to(DEVICE)
        emo = torch.LongTensor([int(emotion_id)]).to(DEVICE)
        audio = self.net_g.infer(
            x,
            x_len,
            sid=sid,
            emotion=emo,
            noise_scale=float(noise_scale),
            noise_scale_w=float(noise_scale_w),
            length_scale=float(length_scale),
        )[0][0, 0].cpu().float().numpy()
        # peak-normalize to avoid clipping, keep some headroom
        peak = float(np.abs(audio).max())
        if peak > 0:
            audio = audio / peak * 0.95
        audio_i16 = (audio * 32767.0).astype(np.int16)
        return self.sampling_rate, audio_i16, used

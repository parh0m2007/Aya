"""Проверка перед публикацией: нет ли в отслеживаемых git файлах секретов,
личного и файлов больше 95 МБ (GitHub не принимает > 100 МБ).

    python scripts/check_public.py                 # индекс (git ls-files)
    python scripts/check_public.py --tree public-main
    python scripts/check_public.py --words scripts/private-words.txt

Код выхода 0 — чисто, 1 — есть находки (строки «путь:строка: причина»).
Сами секреты в отчёт не печатаются.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 95 * 1024 * 1024

PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "похоже на ключ OpenAI/OpenRouter"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"), "похоже на токен GitHub"),
    (re.compile(r"y0_[A-Za-z0-9_-]{20,}|y0__[A-Za-z0-9_-]{20,}"), "похоже на токен Яндекса"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "похоже на токен Telegram-бота"),
    (re.compile(r"\"(api_key|fish_api_key|ym_token|token|bot_token|api_hash)\"\s*:\s*\"[^\"]{8,}\""), "ключ в JSON"),
    (re.compile(r"(?i)(key|token|secret|password)[\"']?\s*[:=]\s*[\"']?[0-9a-f]{32,}"), "длинная hex-строка рядом с key/token/secret"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "закрытый ключ"),
]
BAD_PATHS = [
    (re.compile(r"(^|/)refs_user/"), "голос автора (refs_user)"),
    (re.compile(r"(^|/)aya\.db(-wal|-shm)?$"), "база с перепиской"),
    (re.compile(r"(^|/)\.aya/"), "данные из ~/.aya"),
    (re.compile(r"(^|/)config\.json$"), "config.json с настройками"),
    (re.compile(r"\.(key|pem)$"), "файл ключа"),
    # скриншоты и макеты с настоящей перепиской, памятью и путями автора
    (re.compile(r"^presentation/"), "презентация автора (личные скриншоты)"),
    (re.compile(r"^docs/design/"), "макет с данными автора"),
    (re.compile(r"^\.agents/|^\.opencode/|^graphify-out/"), "служебные файлы инструментов"),
]
ALLOW_PATHS = re.compile(r"^(src-tauri/gen/|node_modules/)|(^|/)(package(-lock)?|tsconfig|tauri\.conf|default)\.json$|/(models|dialogs_vits2|qwen)/.*config\.json$")
TEXT_SUFFIXES = {".py", ".ts", ".js", ".json", ".md", ".txt", ".toml", ".yml", ".yaml", ".ps1", ".cmd", ".bat", ".html", ".css", ".rs", ".cfg", ".ini", ".env", ""}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def files_and_sizes(repo: Path, tree: str | None) -> list[tuple[str, int]]:
    if tree:
        out = []
        for line in git(repo, "ls-tree", "-r", "-l", tree).splitlines():
            meta, path = line.split("\t", 1)
            size = meta.split()[3]
            out.append((path, int(size) if size.isdigit() else 0))
        return out
    paths = [p for p in git(repo, "ls-files", "-z").split("\0") if p]
    return [(p, (repo / p).stat().st_size if (repo / p).is_file() else 0) for p in paths]


def read_text(repo: Path, tree: str | None, path: str) -> str | None:
    if Path(path).suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        if tree:
            return git(repo, "show", f"{tree}:{path}")
        return (repo / path).read_text(encoding="utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return None


def scan(repo: Path, tree: str | None, words: list[str]) -> list[str]:
    found = []
    for path, size in files_and_sizes(repo, tree):
        if size > MAX_BYTES:
            found.append(f"{path}: файл {size // (1024 * 1024)} МБ — больше 95 МБ")
        for rx, why in BAD_PATHS:
            if rx.search(path) and not ALLOW_PATHS.search(path):
                found.append(f"{path}: {why}")
        if size > 5 * 1024 * 1024:
            continue
        text = read_text(repo, tree, path)
        if text is None:
            continue
        low = text.lower()
        for i, line in enumerate(text.splitlines(), 1):
            if "public-ok" in line:  # выдуманное значение в тесте, помечено вручную
                continue
            for rx, why in PATTERNS:
                if rx.search(line):
                    found.append(f"{path}:{i}: {why}")
        for w in words:
            if w in low:
                for i, line in enumerate(text.splitlines(), 1):
                    if w in line.lower():
                        found.append(f"{path}:{i}: личное слово из списка")
    return found


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")  # консоль Windows по умолчанию cp1251
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--tree", default=None)
    ap.add_argument("--words", default=None)
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    wpath = Path(a.words) if a.words else repo / "scripts" / "private-words.txt"
    words = []
    if wpath.is_file():
        words = [w.strip().lower() for w in wpath.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
    found = scan(repo, a.tree, words)
    for f in found:
        print(f)
    print(f"{'чисто' if not found else f'находок: {len(found)}'}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_public.py"


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _add(repo: Path, name: str, data: bytes | str):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_bytes(data)
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)


def _run(repo: Path, *args: str):
    return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), *args], capture_output=True, text=True, encoding="utf-8")


def test_finds_token_and_big_file(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "a.py", 'TOKEN = "ghp_' + "A" * 36 + '"\n')
    big = repo / "big.bin"
    with open(big, "wb") as f:
        f.seek(100 * 1024 * 1024)
        f.write(b"\0")
    subprocess.run(["git", "-C", str(repo), "add", "big.bin"], check=True)
    r = _run(repo)
    assert r.returncode == 1
    found = [line for line in r.stdout.splitlines() if line.strip()]
    assert any(line.startswith("a.py:1:") for line in found) and any(line.startswith("big.bin:") for line in found)
    assert "A" * 36 not in r.stdout  # сам секрет в отчёт не печатаем


def test_finds_author_voice_and_private_words(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "core/voice/f5_ru/refs_user/user_ru.mp3", b"x")
    _add(repo, "notes.md", "Привет, это Секретное Имя\n")
    (repo / "words.txt").write_text("секретное имя\n", encoding="utf-8")
    r = _run(repo, "--words", str(repo / "words.txt"))
    assert r.returncode == 1
    assert "refs_user" in r.stdout and "notes.md:1:" in r.stdout


def test_clean_tree_passes(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "ok.py", "print('hello')\n")
    assert _run(repo).returncode == 0


def test_marked_fake_values_pass(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "t.py", 'cfg = {"api_key": "sk-test-123456789"}  # public-ok: выдуманный ключ для теста\n')
    assert _run(repo).returncode == 0


def test_private_folders_flagged(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "presentation/img/console-chat.png", b"\x89PNG")
    _add(repo, "docs/design/aya-studio.html", "<p>макет</p>")
    r = _run(repo)
    assert r.returncode == 1 and "presentation/" in r.stdout and "docs/design/" in r.stdout

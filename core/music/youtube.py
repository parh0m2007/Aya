"""YouTube: поиск через yt-dlp (без ключа), запасной путь — страница поиска."""
import re
import urllib.parse

from .. import db


def search(query: str, n: int = 5) -> list[dict]:
    query = str(query or "").strip()[:200]
    if not query:
        return []
    try:
        import yt_dlp

        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist", "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False) or {}
        out = [
            {"id": e["id"], "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "", "duration": e.get("duration") or 0}
            for e in (info.get("entries") or [])
            if e and e.get("id")
        ]
        if out:
            return out
    except Exception:  # noqa: BLE001 — нет yt-dlp или он сломался: страница поиска
        pass
    return scrape(query, n)


def scrape(query: str, n: int) -> list[dict]:
    import httpx

    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        html = httpx.get(url, timeout=8, headers={"Accept-Language": "ru,en;q=0.8", "User-Agent": "Mozilla/5.0"}, follow_redirects=True).text
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for m in re.finditer(r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,600}?"title":\{"runs":\[\{"text":"(.*?)"\}', html):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        out.append({"id": vid, "title": m.group(2), "channel": "", "duration": 0})
        if len(out) >= n:
            break
    return out


def pick_song(results: list[dict]) -> dict | None:
    """Первый результат, но не шортс и не многочасовая подборка, если есть выбор."""
    for r in results:
        d = r.get("duration") or 0
        if not d or 60 <= d <= 20 * 60:
            return r
    return results[0] if results else None


def site() -> str:
    return "music" if db.setting("music_youtube_site", "youtube") == "music" else "youtube"


def radio_on() -> bool:
    return db.setting("music_youtube_radio", "1") != "0"


def watch_url(video_id: str, radio: bool = True) -> str:
    base = "https://music.youtube.com" if site() == "music" else "https://www.youtube.com"
    return f"{base}/watch?v={video_id}" + (f"&list=RD{video_id}" if radio else "")

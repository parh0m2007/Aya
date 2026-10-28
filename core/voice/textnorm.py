"""Числа/время/проценты в читаемые слова для TTS (русский разговорный)."""
import re

_ONES = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_ONES_F = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEEN = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]

_THOUSAND = ("тысяча", "тысячи", "тысяч")   # 1, 2-4, 5-20
_HOUR = ("час", "часа", "часов")
_MIN = ("минута", "минуты", "минут")
_PCT = ("процент", "процента", "процентов")


def _plural_kind(n: int) -> int:
    """0 -> форма-5(тысяч), 1 -> единица, 2 -> формы 2-4."""
    n %= 100
    if 11 <= n <= 19:
        return 0
    n %= 10
    if n == 1:
        return 1
    if 2 <= n <= 4:
        return 2
    return 0


def _form(t: tuple, n: int) -> str:
    k = _plural_kind(n)
    return t[2] if k == 0 else (t[0] if k == 1 else t[1])


def _small(n: int, fem: bool = False) -> list[str]:
    """0..999 словами."""
    parts: list[str] = []
    h, rest = divmod(n, 100)
    if h:
        parts.append(_HUNDREDS[h])
    if 10 <= rest < 20:
        parts.append(_TEEN[rest - 10])
        return parts
    if rest >= 20:
        parts.append(_TENS[rest // 10])
    t = rest % 10
    if t:
        parts.append(_ONES_F[t] if fem else _ONES[t])
    return parts


def _words(n: int, fem: bool = False) -> list[str]:
    if fem and n in (1, 2):
        return [_ONES_F[n]]
    parts: list[str] = []
    thousands, rest = divmod(n, 1000)
    if thousands:
        if thousands == 1:
            parts.append(_THOUSAND[0])
        else:
            parts.extend(_small(thousands, fem=True))
            parts.append(_form(_THOUSAND, thousands))
    if rest:
        parts.extend(_small(rest, fem=fem))
    return parts or ["ноль"]


def plain(n: int) -> str:
    return " ".join(_words(n))


def _plain_m(m: re.Match) -> str:
    return plain(int(m.group(0)))


def _pct(m: re.Match) -> str:
    n = int(m.group(1))
    return " ".join(_words(n) + [_form(_PCT, n)])


def _time(m: re.Match) -> str:
    h, mm = int(m.group(1)), int(m.group(2))
    parts = [" ".join(_words(h)) if h != 1 else "час", _form(_HOUR, h)]
    if mm:
        parts += _words(mm, fem=True) + [_form(_MIN, mm)]
    return " ".join(parts)


def _range(m: re.Match) -> str:
    a, b = int(m.group(1)), int(m.group(2))
    return f"с {plain(a)} по {plain(b)}"


_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_PCT_RE = re.compile(r"(\d{1,6})\s*%")
_RANGE_RE = re.compile(r"\b(\d{1,6})\s*[–—-]\s*(\d{1,6})\b")
_NUM_RE = re.compile(r"\b\d{1,9}\b")


def speakable(text: str) -> str:
    text = _TIME_RE.sub(_time, text)
    text = _PCT_RE.sub(_pct, text)
    text = _RANGE_RE.sub(_range, text)
    return _NUM_RE.sub(_plain_m, text)

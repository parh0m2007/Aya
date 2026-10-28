import pytest

from core.voice import textnorm


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "ноль"),
        (1, "один"),
        (2, "два"),
        (21, "двадцать один"),
        (105, "сто пять"),
        (1000, "тысяча"),
        (2002, "две тысячи два"),
        (15500, "пятнадцать тысяч пятьсот"),
        (123456, "сто двадцать три тысячи четыреста пятьдесят шесть"),
    ],
)
def test_plain(n, expected):
    assert textnorm.plain(n) == expected


@pytest.mark.parametrize(
    "src,want",
    [
        ("тебя ждут 15% скидки!", "пятнадцать процентов"),
        ("100% верно", "сто процентов"),
        ("1% погрешность", "один процент"),
        ("в 7:40 встреча", "семь часов сорок минут"),
        ("в 1:05 выход", "час пять минут"),
        ("225 рублей на счету", "двести двадцать пять"),
        ("поставил 3000 шагов", "три тысячи"),
        ("7–9 вечера", "с семь по девять вечера"),
        ("температура 23°C", "двадцать три"),
    ],
)
def test_speakable_markers(src, want):
    out = textnorm.speakable(src)
    assert want in out


def test_no_numbers_untouched():
    assert textnorm.speakable("Привет! Как дела?") == "Привет! Как дела?"

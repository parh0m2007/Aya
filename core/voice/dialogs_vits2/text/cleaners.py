import re

_whitespace_re = re.compile(r"\s+")


def collapse_whitespace(text):
    return re.sub(_whitespace_re, " ", text)


def basic_cleaners(text):
    """Lowercase and collapse whitespace, without transliteration.

    This matches the preprocessing used to train the Dialogs-RU model:
    raw Russian text is kept as-is (Cyrillic), only normalized in case and
    spacing. Characters outside the 73-symbol vocabulary are dropped later
    by ``text_to_sequence``.
    """
    return collapse_whitespace(text.lower()).strip()

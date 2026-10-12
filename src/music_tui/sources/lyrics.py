"""Convert the sources' decrypted line-timed LRC text into shared lyric values."""

import re
from html import unescape

from ..models import LyricLine, Lyrics

TIMESTAMP = re.compile(r"\[(\d+):(\d{1,2})(?:[.,](\d{1,3}))?\]")
METADATA = re.compile(r"^\[[a-zA-Z]+:.*\]$")
OFFSET = re.compile(r"\[offset:([+-]?\d+)\]", re.IGNORECASE)


def _lines(content: str) -> tuple[list[tuple[float, str]], list[str]]:
    """Expand repeated LRC tags, apply millisecond offsets, and retain plain text."""
    timed = []
    plain = []
    offset_match = OFFSET.search(content)
    offset = int(offset_match[1]) / 1000 if offset_match else 0
    for raw in unescape(content).splitlines():
        matches = list(TIMESTAMP.finditer(raw))
        if matches:
            text = TIMESTAMP.sub("", raw).strip()
            for match in matches:
                seconds = int(match[1]) * 60 + int(match[2])
                seconds += int((match[3] or "0").ljust(3, "0")) / 1000
                timed.append((max(0, seconds + offset), text))
        elif raw.strip() and not METADATA.fullmatch(raw.strip()):
            plain.append(raw.strip())
    timed.sort(key=lambda item: item[0])
    return timed, plain


def parse_lyrics(original: str, translated: str = "") -> Lyrics:
    """Pair translations by timestamp while preserving original lyric order."""
    timed, plain = _lines(original)
    translations, translated_plain = _lines(translated)
    if timed:
        by_time: dict[float, list[str]] = {}
        for seconds, text in translations:
            if text:
                by_time.setdefault(seconds, []).append(text)
        return Lyrics(
            tuple(
                LyricLine(seconds, text, " / ".join(by_time.get(seconds, ())))
                for seconds, text in timed
            )
        )
    return Lyrics(
        tuple(
            LyricLine(0, text, translated_plain[index] if index < len(translated_plain) else "")
            for index, text in enumerate(plain)
        ),
        synced=False,
    )

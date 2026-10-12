"""Verify timed, translated and plain lyrics using offline platform responses."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from music_tui.models import LyricLine, Lyrics, Track
from music_tui.sources.lyrics import parse_lyrics
from music_tui.sources.netease import NetEaseMusic
from music_tui.sources.qqmusic import QQMusic

TRACK = Track("123", "歌曲", "歌手", 180)


def test_lrc_pairs_translations_expands_tags_and_applies_offsets():
    """Keep repeated chorus times, fractional seconds, blank lines and translations."""
    result = parse_lyrics(
        "[ar:歌手]\n[offset:-250]\n[00:02.50]Second &amp; line\n"
        "[00:01.5][01:01.500]Chorus\n[00:03.000]",
        "[offset:-250]\n[00:01.500]副歌\n[00:02.5]第二句\n[01:01.50]副歌",
    )
    assert result == Lyrics(
        (
            LyricLine(1.25, "Chorus", "副歌"),
            LyricLine(2.25, "Second & line", "第二句"),
            LyricLine(2.75, ""),
            LyricLine(61.25, "Chorus", "副歌"),
        )
    )


@pytest.mark.parametrize(
    "original, translated, expected",
    [
        ("", "", Lyrics((), synced=False)),
        ("[ar:歌手]\n[ti:歌曲]", "", Lyrics((), synced=False)),
        (
            "First\nSecond",
            "第一句",
            Lyrics((LyricLine(0, "First", "第一句"), LyricLine(0, "Second")), synced=False),
        ),
    ],
)
def test_plain_or_missing_lyrics_keep_text_without_fabricating_timing(
    original, translated, expected
):
    """Represent absence separately from lyrics whose source provides no timestamps."""
    assert parse_lyrics(original, translated) == expected


async def test_qq_lyrics_consume_sdk_decrypted_response(monkeypatch, tmp_path):
    """Request translation through the SDK and expose only shared lyric models."""
    get_lyric = AsyncMock(
        return_value=SimpleNamespace(lyric="[00:01.00]Hello", trans="[00:01.0]你好")
    )
    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(lyric=SimpleNamespace(get_lyric=get_lyric)),
    )
    music = QQMusic(tmp_path)
    assert await music.lyrics(TRACK) == Lyrics((LyricLine(1, "Hello", "你好"),))
    get_lyric.assert_awaited_once_with("123", trans=True)


@pytest.mark.parametrize(
    "payload, expected",
    [
        (
            {
                "code": 200,
                "lrc": {"lyric": "[00:01.00]Hello"},
                "tlyric": {"lyric": "[00:01.0]你好"},
            },
            Lyrics((LyricLine(1, "Hello", "你好"),)),
        ),
        ({"code": 200, "nolyric": True}, Lyrics((), synced=False)),
        ({"code": 200, "uncollected": True}, Lyrics((), synced=False)),
    ],
)
async def test_enhanced_lyrics_request_and_absent_content(monkeypatch, tmp_path, payload, expected):
    """Parse LRC responses while recognizing the service's missing-lyrics variants."""
    original_client = httpx.AsyncClient

    def handler(request):
        """Check the public lyric request without requiring a logged-in account."""
        assert request.url.path == "/lyric"
        assert json.loads(request.content)["id"] == "123"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "music_tui.sources.netease.httpx.AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    music = NetEaseMusic(tmp_path, api_url="http://api.test")
    try:
        assert await music.lyrics(TRACK) == expected
    finally:
        await music.close()


async def test_lyrics_network_failure_is_not_reported_as_missing_content(monkeypatch, tmp_path):
    """Preserve request failures so the UI can offer a retry rather than no-lyrics text."""
    get_lyric = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(lyric=SimpleNamespace(get_lyric=get_lyric)),
    )
    with pytest.raises(TimeoutError):
        await QQMusic(tmp_path).lyrics(TRACK)

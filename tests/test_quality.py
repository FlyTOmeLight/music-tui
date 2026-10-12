"""Verify authorized audio formats and media variant selection."""

from types import SimpleNamespace

from music_tui.models import Quality, Track
from music_tui.sources.qqmusic import QQMusic


async def test_available_quality_uses_authorized_paths_and_correct_variant(monkeypatch, tmp_path):
    """Verify quality discovery excludes unusable paths and selects the correct media variants."""
    requested = []

    async def get_song_urls(infos):
        """Return mixed authorized, denied, and missing paths in a shuffled response."""
        requested.extend(infos)
        # Response order differs from the request; missing paths and denied results are excluded.
        return SimpleNamespace(
            data=[
                SimpleNamespace(filename="M500base.mp3", purl="standard.mp3", result=0),
                SimpleNamespace(filename="M800base.mp3", purl="", result=104003),
                SimpleNamespace(filename="RS01base.flac", purl="denied.flac", result=104003),
                SimpleNamespace(filename="RS01other.flac", purl="missing.flac", result=0),
                SimpleNamespace(filename="AI00master.flac", purl="master.flac", result=0),
                SimpleNamespace(filename="Q003spatial.ogg", purl="", result=0),
                SimpleNamespace(filename="F000base.flac", purl="lossless.flac", result=0),
            ]
        )

    async def get_cdn_dispatch():
        """Return the fixed CDN prefix used to assemble test stream URLs."""
        return SimpleNamespace(sip=["https://audio.example.com/"])

    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(
            song=SimpleNamespace(
                get_song_urls=get_song_urls,
                get_cdn_dispatch=get_cdn_dispatch,
            )
        ),
    )
    monkeypatch.setattr(
        "music_tui.sources.qqmusic.url_reachable",
        lambda url: not url.endswith("missing.flac"),
    )
    variants = [""] * 25
    variants[3], variants[4], variants[24] = "master", "enhanced", "spatial"
    track = Track("song", "歌曲", "歌手", 120, "base", variants=tuple(variants))
    music = QQMusic(tmp_path)
    assert await music.available_qualities(track) == [
        Quality.MASTER,
        Quality.LOSSLESS,
        Quality.STANDARD,
    ]
    by_prefix = {info.file_type.s: info.media_mid for info in requested}
    assert by_prefix["AI00"] == "master"
    assert by_prefix["Q000"] == "enhanced"
    assert by_prefix["Q003"] == "spatial"
    assert by_prefix["RS01"] == "base"

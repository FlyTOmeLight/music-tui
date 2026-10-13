"""Verify authorized audio formats and media variant selection."""

from contextlib import nullcontext
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from test_playback_features import FeatureAudio

from music_tui.app import MusicApp
from music_tui.models import Quality, QualityUnavailableError, Track
from music_tui.sources.qqmusic import QQMusic, url_reachable


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


@pytest.mark.parametrize(
    "data, code",
    [
        ([], "空响应"),
        ([SimpleNamespace(purl="", result=0)], "0"),
        ([SimpleNamespace(purl="denied.flac", result=104003)], "104003"),
    ],
)
async def test_qq_unavailable_or_denied_streams_allow_lower_tiers(
    monkeypatch, tmp_path, data, code
):
    """Expose a typed restriction when QQ withholds audio or rejects its authorization."""

    async def get_song_urls(infos, **options):
        """Return one song's missing or denied authorization result."""
        return SimpleNamespace(data=data)

    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(song=SimpleNamespace(get_song_urls=get_song_urls)),
    )
    music = QQMusic(tmp_path)
    with pytest.raises(QualityUnavailableError) as failure:
        await music.stream_url(Track("song", "歌曲", "歌手", 120), Quality.LOSSLESS)
    assert f"QQ 结果码 {code}" in str(failure.value)
    assert "会员" not in str(failure.value)


async def test_qq_missing_premium_variant_allows_lower_tiers(monkeypatch, tmp_path):
    """Treat absent premium media metadata as unavailable without making an API request."""
    monkeypatch.setattr("music_tui.sources.qqmusic.Client", lambda **kwargs: SimpleNamespace())
    music = QQMusic(tmp_path)
    with pytest.raises(QualityUnavailableError, match="暂无臻品母带音源"):
        await music.stream_url(Track("song", "歌曲", "歌手", 120), Quality.MASTER)


@pytest.mark.parametrize("missing_cdn", [False, True])
async def test_qq_request_or_cdn_failures_are_not_tier_restrictions(
    monkeypatch, tmp_path, missing_cdn
):
    """Preserve API failures and incomplete CDN responses without triggering tier retries."""

    async def get_song_urls(infos, **options):
        """Return an authorized stream or raise a global API failure."""
        if not missing_cdn:
            raise ValueError("登录失效")
        return SimpleNamespace(data=[SimpleNamespace(purl="song.flac", result=0)])

    async def get_cdn_dispatch():
        """Return an incomplete CDN service response."""
        return SimpleNamespace(sip=[])

    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(
            song=SimpleNamespace(get_song_urls=get_song_urls, get_cdn_dispatch=get_cdn_dispatch)
        ),
    )
    music = QQMusic(tmp_path)
    with pytest.raises(ValueError) as failure:
        await music.stream_url(Track("song", "歌曲", "歌手", 120), Quality.LOSSLESS)
    assert not isinstance(failure.value, QualityUnavailableError)


@pytest.mark.parametrize("code", [404, 410, 403, 500, "timeout", "invalid-cdn"])
async def test_qq_cdn_missing_files_downgrade_without_hiding_other_failures(
    monkeypatch, tmp_path, code
):
    """Check actual QQ source paths before mpv; retry lower tiers only for CDN 404 or 410."""
    requested = []

    async def get_song_urls(infos, **options):
        """Authorize both Hi-Res and lossless even when the Hi-Res CDN file is absent."""
        prefix = infos[0].file_type.s
        requested.append(prefix)
        path = "missing.flac" if prefix == "RS01" else "available.flac"
        return SimpleNamespace(data=[SimpleNamespace(purl=f"{path}?key=private", result=0)])

    async def get_cdn_dispatch():
        """Return a real-shaped CDN prefix, or simulate invalid service configuration."""
        return SimpleNamespace(
            sip=["invalid-cdn" if code == "invalid-cdn" else "https://audio.test/"]
        )

    def open_audio(request, *, timeout):
        """Raise the configured Hi-Res failure and authorize the lower-tier file."""
        assert request.get_method() == "HEAD" and timeout == 8
        if "missing.flac" in request.full_url:
            if code == "timeout":
                raise TimeoutError("https://audio.test/missing.flac?key=private")
            raise HTTPError(request.full_url, code, "CDN error", {}, None)
        return nullcontext(SimpleNamespace(status=200))

    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(
            credential=SimpleNamespace(musicid=0, musickey=""),
            song=SimpleNamespace(get_song_urls=get_song_urls, get_cdn_dispatch=get_cdn_dispatch),
        ),
    )
    monkeypatch.setattr("music_tui.sources.qqmusic.urlopen", open_audio)
    player = FeatureAudio()
    music = QQMusic(tmp_path)
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.quality = Quality.HIRES
        app.playback_queue.tracks = [Track("song", "歌曲", "歌手", 120, "base")]
        await app.play_index(0).wait()
        if code in (404, 410):
            assert requested == ["RS01", "F000"]
            assert player.loaded == ["https://audio.test/available.flac?key=private"]
            assert app.current_quality == Quality.LOSSLESS and app.quality == Quality.HIRES
            assert "已自动切换到 SQ 无损" in app.last_status_message
        else:
            assert requested == ["RS01"] and not player.loaded
            assert app.current is None and app.current_quality is None
            assert "F5 重试" in app.last_status_message
        assert (
            "private" not in app.last_status_message and "audio.test" not in app.last_status_message
        )


@pytest.mark.parametrize("code", [200, 404, 410, 403, 500])
def test_qq_head_probe_only_treats_missing_files_as_unavailable(monkeypatch, code):
    """Classify missing resources while retaining CDN permission and server errors."""

    def open_audio(request, *, timeout):
        """Return success or urllib's native HTTP exception for the tested status."""
        if code != 200:
            raise HTTPError(request.full_url, code, "CDN error", {}, None)
        return nullcontext(SimpleNamespace(status=code))

    monkeypatch.setattr("music_tui.sources.qqmusic.urlopen", open_audio)
    if code in (403, 500):
        with pytest.raises(HTTPError) as failure:
            url_reachable("https://audio.test/song.flac")
        assert failure.value.code == code
    else:
        assert url_reachable("https://audio.test/song.flac") == (code == 200)


@pytest.mark.parametrize("code", [404, 410, 403, 500])
async def test_qq_quality_discovery_does_not_hide_cdn_errors(monkeypatch, tmp_path, code):
    """Exclude missing files while surfacing network or CDN failures during tier discovery."""

    async def get_song_urls(infos):
        """Authorize Hi-Res and lossless files with their exact requested filename prefixes."""
        return SimpleNamespace(
            data=[
                SimpleNamespace(filename="RS01base.flac", purl="missing.flac", result=0),
                SimpleNamespace(filename="F000base.flac", purl="available.flac", result=0),
            ]
        )

    async def get_cdn_dispatch():
        """Return the CDN shared by the two authorized quality files."""
        return SimpleNamespace(sip=["https://audio.test/"])

    def open_audio(request, *, timeout):
        """Simulate one missing or inaccessible tier while serving the lower-tier file."""
        if request.full_url.endswith("missing.flac"):
            raise HTTPError(request.full_url, code, "CDN error", {}, None)
        return nullcontext(SimpleNamespace(status=200))

    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(
            song=SimpleNamespace(get_song_urls=get_song_urls, get_cdn_dispatch=get_cdn_dispatch)
        ),
    )
    monkeypatch.setattr("music_tui.sources.qqmusic.urlopen", open_audio)
    music = QQMusic(tmp_path)
    track = Track("song", "歌曲", "歌手", 120, "base")
    if code in (403, 500):
        with pytest.raises(HTTPError) as failure:
            await music.available_qualities(track)
        assert failure.value.code == code
    else:
        assert await music.available_qualities(track) == [Quality.LOSSLESS]

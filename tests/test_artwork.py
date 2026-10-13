"""Exercise terminal artwork rendering and bounded downloads without network access."""

import asyncio
from io import BytesIO

import httpx
import pytest
from PIL import Image
from textual.app import App, ComposeResult

from music_tui import artwork
from music_tui.artwork import AlbumArtwork


class ArtworkHarness(App):
    """Host a cover widget with the same theme-backed surface as the player."""

    def compose(self) -> ComposeResult:
        """Mount one cover at its default dimensions."""
        yield AlbumArtwork()


def png_bytes(color: tuple[int, ...]) -> bytes:
    """Encode a small solid image to check visible pixel colors."""
    data = BytesIO()
    Image.new("RGBA", (8, 8), color).save(data, format="PNG")
    return data.getvalue()


def mock_downloads(monkeypatch, handler) -> None:
    """Route the widget's actual HTTPX client through an offline transport."""
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def create_client(**kwargs):
        """Retain production client options while replacing its network transport."""
        return client_class(transport=transport, **kwargs)

    monkeypatch.setattr(artwork.httpx, "AsyncClient", create_client)


async def test_cover_download_renders_color_and_reuses_same_url(monkeypatch):
    """Show 20 by 10 terminal cells and avoid downloading a repeated song cover."""
    requests = []

    def respond(request):
        """Return a red album image and record actual HTTP requests."""
        requests.append(request)
        return httpx.Response(200, content=png_bytes((255, 0, 0, 255)))

    mock_downloads(monkeypatch, respond)
    app = ArtworkHarness()
    async with app.run_test() as pilot:
        cover = app.query_one(AlbumArtwork)
        assert cover.render().plain == "暂无封面"
        cover.set_cover("https://covers.example/red.png")
        await app.workers.wait_for_complete()
        await pilot.pause()
        rendered = cover.render()
        assert len(rendered.plain.splitlines()) == 10
        assert all(len(row) == 20 for row in rendered.plain.splitlines())
        assert rendered.spans[0].style.color.triplet == (255, 0, 0)
        assert rendered.spans[0].style.bgcolor.triplet == (255, 0, 0)
        cover.set_cover("https://covers.example/red.png")
        await pilot.pause()
        assert len(requests) == 1
        cover.set_cover("")
        assert cover.render().plain == "暂无封面"


async def test_transparent_cover_tracks_theme_without_downloading_again(monkeypatch):
    """Blend transparent pixels with the current surface after a theme change."""
    requests = []

    def respond(request):
        """Return a transparent image to expose the widget's background color."""
        requests.append(request)
        return httpx.Response(200, content=png_bytes((0, 0, 0, 0)))

    mock_downloads(monkeypatch, respond)
    app = ArtworkHarness()
    async with app.run_test() as pilot:
        cover = app.query_one(AlbumArtwork)
        cover.set_cover("https://covers.example/transparent.png")
        await app.workers.wait_for_complete()
        dark_color = cover.render().spans[0].style.color.triplet
        app.theme = "textual-light"
        await pilot.pause()
        light_color = cover.render().spans[0].style.color.triplet
        assert dark_color != light_color
        assert len(requests) == 1


@pytest.mark.parametrize("status, data", [(404, b"missing"), (200, b"invalid image")])
async def test_unavailable_or_invalid_cover_keeps_widget_usable(monkeypatch, status, data):
    """Turn network and decode failures into a local message, then allow a new cover."""

    def respond(request):
        """Provide a failure for the first URL and a valid image for the next song."""
        if request.url.path == "/bad":
            return httpx.Response(status, content=data)
        return httpx.Response(200, content=png_bytes((0, 255, 0, 255)))

    mock_downloads(monkeypatch, respond)
    app = ArtworkHarness()
    async with app.run_test():
        cover = app.query_one(AlbumArtwork)
        cover.set_cover("https://covers.example/bad")
        await app.workers.wait_for_complete()
        assert cover.render().plain == "封面加载失败"
        cover.set_cover("https://covers.example/good")
        await app.workers.wait_for_complete()
        assert cover.render().spans[0].style.color.triplet == (0, 255, 0)


async def test_response_size_is_limited_even_without_content_length(monkeypatch):
    """Reject downloaded bytes beyond the cover limit without trusting HTTP headers."""
    monkeypatch.setattr(artwork, "MAX_COVER_BYTES", 16)

    def respond(request):
        """Return enough data to exceed the configured bound."""
        return httpx.Response(200, content=b"x" * 17)

    mock_downloads(monkeypatch, respond)
    app = ArtworkHarness()
    async with app.run_test():
        cover = app.query_one(AlbumArtwork)
        cover.set_cover("https://covers.example/large")
        await app.workers.wait_for_complete()
        assert cover.render().plain == "封面加载失败"


async def test_song_change_cancels_slow_cover_without_replacing_new_image(monkeypatch):
    """Cancel an old response so that the next song owns the visible artwork."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def respond(request):
        """Block the previous cover until cancellation and immediately serve the new one."""
        if request.url.path == "/old":
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return httpx.Response(200, content=png_bytes((0, 0, 255, 255)))

    mock_downloads(monkeypatch, respond)
    app = ArtworkHarness()
    async with app.run_test():
        cover = app.query_one(AlbumArtwork)
        cover.set_cover("https://covers.example/old")
        async with asyncio.timeout(1):
            await started.wait()
        cover.set_cover("https://covers.example/new")
        await app.workers.wait_for_complete(
            worker for worker in app.workers if not worker.is_cancelled
        )
        assert cancelled.is_set()
        assert cover.render().spans[0].style.color.triplet == (0, 0, 255)

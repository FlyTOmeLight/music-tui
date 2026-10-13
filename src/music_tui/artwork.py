"""Render album artwork with true-color half blocks in ordinary terminals."""

import asyncio
from io import BytesIO

import httpx
from PIL import Image, ImageOps
from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual import work
from textual.events import Resize
from textual.widgets import Static
from textual.worker import Worker

MAX_COVER_BYTES = 8 * 1024 * 1024
MAX_COVER_PIXELS = 16_000_000


def _decode_cover(data: bytes) -> Image.Image:
    """Decode a bounded cover and retain transparency for the current theme."""
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > MAX_COVER_PIXELS:
            raise ValueError("封面图片过大")
        return ImageOps.exif_transpose(source).convert("RGBA")


class AlbumArtwork(Static):
    """Show one cover without allowing a slow or failed request to affect playback."""

    DEFAULT_CSS = """
    AlbumArtwork {
        width: 20; height: 10; content-align: center middle;
        color: $text-muted; background: $surface;
    }
    """

    def __init__(self, *, id: str | None = None, classes: str | None = None):
        """Start with an empty cover and download only after a URL is supplied."""
        super().__init__(markup=False, id=id, classes=classes)
        self._url = ""
        self._image: Image.Image | None = None
        self._message = "暂无封面"
        self._worker: Worker[None] | None = None

    def set_cover(self, url: str) -> None:
        """Load a changed URL, clearing the previous song's image immediately."""
        if url == self._url:
            return
        self._url = url
        if self._worker is not None:
            self._worker.cancel()
        self._image = None
        self._message = "正在加载封面…" if url else "暂无封面"
        self.refresh()
        if url and self.is_mounted:
            self._worker = self._load_cover(url)

    def on_mount(self) -> None:
        """Load a URL supplied before the widget was mounted."""
        if self._url:
            self._worker = self._load_cover(self._url)

    def on_unmount(self) -> None:
        """Cancel an outstanding request when the listening page closes."""
        if self._worker is not None:
            self._worker.cancel()

    def on_resize(self, event: Resize) -> None:
        """Resample cached artwork when the surrounding layout changes size."""
        self.refresh()

    @work(exclusive=True, group="album-artwork")
    async def _load_cover(self, url: str) -> None:
        """Bound network time and response size, then discard superseded results."""
        try:
            async with asyncio.timeout(5):
                async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_COVER_BYTES:
                                raise ValueError("封面文件过大")
                cover = await asyncio.to_thread(_decode_cover, bytes(data))
        except (httpx.HTTPError, OSError, ValueError, TimeoutError, Image.DecompressionBombError):
            if self.is_mounted and url == self._url:
                self._message = "封面加载失败"
                self.refresh()
            return
        if self.is_mounted and url == self._url:
            self._image = cover
            self.refresh()

    def render(self) -> Text:
        """Pair vertical pixels into terminal cells and blend alpha with the theme."""
        if self._image is None:
            return Text(self._message)
        width = max(1, self.content_size.width)
        rows = max(1, self.content_size.height)
        image = ImageOps.fit(self._image, (width, rows * 2), method=Image.Resampling.LANCZOS)
        background = self.background_colors[1]
        canvas = Image.new("RGBA", image.size, (background.r, background.g, background.b, 255))
        image = Image.alpha_composite(canvas, image).convert("RGB")
        result = Text(no_wrap=True)
        for y in range(0, rows * 2, 2):
            for x in range(width):
                result.append(
                    "▀",
                    Style(
                        color=Color.from_rgb(*image.getpixel((x, y))),
                        bgcolor=Color.from_rgb(*image.getpixel((x, y + 1))),
                    ),
                )
            if y < rows * 2 - 2:
                result.append("\n")
        return result

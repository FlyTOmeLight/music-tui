"""Own one mpv process and its asynchronous JSON IPC connection."""

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any


class Player:
    def __init__(self, *, audio_output: str | None = None):
        """Initialize mpv connection state and an optional audio output override."""
        self.audio_output = audio_output
        self.process: asyncio.subprocess.Process | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.reader_task: asyncio.Task | None = None
        self.directory: tempfile.TemporaryDirectory | None = None
        self.pending: dict[int, asyncio.Future] = {}
        self.request_id = 0
        self.entry_id: int | None = None
        self.properties: dict[str, Any] = {"volume": 70, "pause": False, "time-pos": 0}
        self.events: asyncio.Queue[dict] = asyncio.Queue()

    async def start(self) -> None:
        """Launch mpv, connect to its socket, and subscribe to playback properties."""
        self.directory = tempfile.TemporaryDirectory(prefix="qqm-", dir="/tmp")
        socket = str(Path(self.directory.name) / "mpv.sock")
        arguments = [
            "mpv",
            "--no-config",
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            "--audio-display=no",
            "--volume=70",
            "--network-timeout=20",
            f"--input-ipc-server={socket}",
        ]
        if self.audio_output:
            arguments.append(f"--ao={self.audio_output}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            async with asyncio.timeout(8):
                while True:
                    if self.process.returncode is not None:
                        raise RuntimeError("mpv 启动失败")
                    try:
                        reader, self.writer = await asyncio.open_unix_connection(socket)
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        await asyncio.sleep(0.03)
            self.reader_task = asyncio.create_task(self._read(reader))
            for number, name in enumerate(
                ("time-pos", "duration", "pause", "volume", "paused-for-cache", "idle-active")
            ):
                await self.command("observe_property", number, name)
        except BaseException:
            await self.close()
            raise

    async def _read(self, reader: asyncio.StreamReader) -> None:
        """Route mpv responses and events, failing pending requests on disconnect."""
        try:
            while line := await reader.readline():
                event = json.loads(line)
                if "request_id" in event:
                    future = self.pending.get(event["request_id"])
                    if future and not future.done():
                        if event.get("error") == "success":
                            future.set_result(event.get("data"))
                        else:
                            future.set_exception(RuntimeError(f"mpv: {event.get('error')}"))
                elif event.get("event") == "property-change":
                    self.properties[event["name"]] = event.get("data")
                else:
                    self.events.put_nowait(event)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("mpv 连接已关闭"))
            self.events.put_nowait({"event": "disconnected"})

    async def command(self, *arguments: Any) -> Any:
        """Send one IPC request and await its matching response within five seconds."""
        if self.writer is None or self.writer.is_closing():
            raise RuntimeError("播放器尚未就绪")
        self.request_id += 1
        request_id = self.request_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.writer.write(
                (json.dumps({"command": arguments, "request_id": request_id}) + "\n").encode()
            )
            await self.writer.drain()
            return await asyncio.wait_for(future, timeout=5)
        finally:
            self.pending.pop(request_id, None)

    async def load(self, url: str, *, start: float = 0, paused: bool = False) -> None:
        """Replace the current track while applying its start position and pause state."""
        await self.command("loadfile", url, "replace", -1, {"start": str(max(0, start))})
        self.entry_id = await self.command("get_property", "playlist/0/id")
        await self.command("set_property", "pause", paused)

    async def close(self) -> None:
        """Close IPC, stop the reader and mpv process, and remove its socket directory."""
        if self.writer:
            self.writer.close()
            with contextlib.suppress(ConnectionError):
                await self.writer.wait_closed()
            self.writer = None
        if self.reader_task:
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader_task
            self.reader_task = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.directory:
            self.directory.cleanup()
            self.directory = None

"""Exercise mpv playback and resource cleanup using a local silent audio file."""

import asyncio
import shutil
import wave
from pathlib import Path

import pytest

from music_tui.player import Player


async def until(predicate):
    """Wait up to four seconds for an observable player condition."""
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(0.02)


@pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv is required")
async def test_real_mpv_play_pause_seek_volume_eof_and_cleanup(tmp_path):
    """Verify real mpv controls, EOF delivery, process exit, and temporary socket cleanup."""
    audio = tmp_path / "sample.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000 * 3)
    player = Player(audio_output="null")
    try:
        await player.start()
        process = player.process
        directory = player.directory.name
        await player.load(str(audio))
        await until(lambda: (player.properties.get("time-pos") or 0) > 0)
        await player.command("cycle", "pause")
        await until(lambda: player.properties.get("pause"))
        await player.command("set_property", "volume", 25)
        await until(lambda: player.properties.get("volume") == 25)
        await player.load(str(audio), start=1.5, paused=True)
        await until(
            lambda: (
                player.properties.get("pause") and (player.properties.get("time-pos") or 0) >= 1.4
            )
        )
        # Discard the stop event from replacing the previous audio file.
        while not player.events.empty():
            player.events.get_nowait()
        await player.command("seek", 2.5, "absolute")
        await player.command("cycle", "pause")
        async with asyncio.timeout(4):
            while True:
                event = await player.events.get()
                if event.get("event") == "end-file":
                    assert event["reason"] == "eof"
                    break
    finally:
        await player.close()
    assert process.returncode is not None
    assert not Path(directory).exists()


@pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv is required")
async def test_real_mpv_restarts_after_disconnect_and_restores_paused_playback(tmp_path):
    """Restore position, pause, and volume after real mpv exits and release both sockets."""
    audio = tmp_path / "restart.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000 * 5)
    player = Player(audio_output="null")
    new_process = None
    new_directory = None
    try:
        await player.start()
        old_process = player.process
        old_directory = Path(player.directory.name)
        await player.load(str(audio))
        await until(lambda: (player.properties.get("time-pos") or 0) > 0.5)
        await player.command("set_property", "pause", True)
        await player.command("set_property", "volume", 31)
        await until(
            lambda: player.properties.get("pause") and player.properties.get("volume") == 31
        )
        position = await player.command("get_property", "time-pos")
        old_process.terminate()
        async with asyncio.timeout(4):
            while (await player.events.get()).get("event") != "disconnected":
                pass
            await old_process.wait()
        await player.close()
        assert not old_directory.exists()
        assert player.writer is None
        assert player.reader_task is None
        assert not player.pending

        await player.start()
        new_process = player.process
        new_directory = Path(player.directory.name)
        assert new_process.pid != old_process.pid
        assert new_process.returncode is None
        assert new_directory != old_directory
        await player.command("set_property", "volume", 31)
        await player.load(str(audio), start=position, paused=True)
        await until(
            lambda: (
                player.properties.get("pause")
                and player.properties.get("volume") == 31
                and abs((player.properties.get("time-pos") or 0) - position) < 0.1
            )
        )
        assert await player.command("get_property", "pause") is True
        assert await player.command("get_property", "volume") == 31
        assert await player.command("get_property", "time-pos") == pytest.approx(position, abs=0.1)
    finally:
        await player.close()
    assert new_process.returncode is not None
    assert not new_directory.exists()

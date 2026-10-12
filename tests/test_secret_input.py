"""Exercise hidden private-key paste through a real terminal without account secrets."""

import contextlib
import os
import pty
import select
import stat
import subprocess
import sys
import termios
import time

import pytest


@contextlib.contextmanager
def secret_terminal(target):
    """Run only the private-key reader and close its process and terminal afterward."""
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    process = subprocess.Popen(
        [sys.executable, "-m", "music_tui.netease_setup", "read-private-key", str(target)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    try:
        deadline = time.monotonic() + 3
        while termios.tcgetattr(slave)[3] & termios.ICANON:
            assert process.poll() is None, "Private-key reader exited before accepting input"
            assert time.monotonic() < deadline, "Private-key reader kept canonical line buffering"
            select.select([], [], [], 0.01)
        yield process, master, slave, original
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        os.close(master)
        os.close(slave)


def terminal_output(process, master):
    """Collect reader output until completion with a short deadline for stalled input."""
    output = bytearray()
    deadline = time.monotonic() + 3
    while process.poll() is None:
        assert time.monotonic() < deadline, "Private-key reader did not finish after Enter"
        if select.select([master], [], [], 0.05)[0]:
            output.extend(os.read(master, 8192))
    while select.select([master], [], [], 0.01)[0]:
        chunk = os.read(master, 8192)
        if not chunk:
            break
        output.extend(chunk)
    return bytes(output)


def paste_into_terminal(master, content):
    """Drain terminal progress while sending input so the PTY cannot fill in either direction."""
    output = bytearray()
    remaining = memoryview(content)
    deadline = time.monotonic() + 3
    os.set_blocking(master, False)
    try:
        while remaining:
            assert time.monotonic() < deadline, "Private-key terminal did not consume pasted input"
            readable, writable, _ = select.select([master], [master], [], 0.05)
            if readable:
                output.extend(os.read(master, 8192))
            if writable:
                try:
                    count = os.write(master, remaining[:256])
                except BlockingIOError:
                    continue
                remaining = remaining[count:]
    finally:
        os.set_blocking(master, True)
    return bytes(output)


def assert_terminal_restored(slave, original):
    """Compare terminal settings while ignoring the kernel's transient pending-input flag."""
    restored = termios.tcgetattr(slave)
    restored[3] &= ~getattr(termios, "PENDIN", 0)
    original[3] &= ~getattr(termios, "PENDIN", 0)
    assert restored == original


@pytest.mark.parametrize("size", [1700, 3500])
def test_long_secret_paste_finishes_without_echo(tmp_path, size):
    """Accept a Base64-sized line beyond macOS's canonical terminal buffer limit."""
    secret = "A" * size
    with secret_terminal(tmp_path) as (process, master, slave, original):
        output = paste_into_terminal(master, secret.encode() + b"\n")
        output += terminal_output(process, master)
        assert process.returncode == 0
        assert_terminal_restored(slave, original)
    saved = tmp_path / "private-key-input"
    assert saved.read_text().rstrip("\n") == secret
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert secret.encode() not in output


def test_multiline_pem_paste_keeps_complete_input(tmp_path):
    """Read an entire PEM block rather than stopping at its first newline."""
    secret = "-----BEGIN PRIVATE KEY-----\n" + "B" * 80 + "\n-----END PRIVATE KEY-----"
    with secret_terminal(tmp_path) as (process, master, _, _):
        output = paste_into_terminal(master, secret.encode() + b"\n")
        output += terminal_output(process, master)
        assert process.returncode == 0
    assert (tmp_path / "private-key-input").read_text().rstrip("\n") == secret
    assert b"B" * 80 not in output


def test_bracketed_paste_waits_for_explicit_enter(tmp_path):
    """Treat pasted newlines as content and wait for Enter outside the paste markers."""
    secret = "-----BEGIN PRIVATE KEY-----\n" + "C" * 80 + "\n-----END PRIVATE KEY-----\n"
    with secret_terminal(tmp_path) as (process, master, _, _):
        output = paste_into_terminal(master, b"\x1b[200~" + secret.encode() + b"\x1b[201~")
        select.select([], [], [], 0.1)
        assert process.poll() is None
        assert not (tmp_path / "private-key-input").exists()
        os.write(master, b"\n")
        output += terminal_output(process, master)
        assert process.returncode == 0
    assert (tmp_path / "private-key-input").read_text().rstrip("\n") == secret.rstrip("\n")
    assert b"C" * 80 not in output


def test_interrupt_restores_terminal_without_writing_secret(tmp_path):
    """Restore echo and canonical input when Ctrl-C interrupts the reader."""
    with secret_terminal(tmp_path) as (process, master, slave, original):
        output = paste_into_terminal(master, b"D" * 1700 + b"\x03")
        output += terminal_output(process, master)
        assert process.returncode != 0
        assert_terminal_restored(slave, original)
    assert not (tmp_path / "private-key-input").exists()
    assert b"D" * 1700 not in output

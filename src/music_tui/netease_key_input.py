"""Read long or pasted NetEase private keys without terminal line limits or secret echo."""

import os
import sys
import termios


def read_private_key() -> str:
    """Accept Base64, a PEM block or a file path, restoring the terminal on every exit."""
    descriptor = sys.stdin.fileno()
    if not os.isatty(descriptor):
        raise ValueError("请在交互式终端输入私钥")
    previous = termios.tcgetattr(descriptor)
    mode = termios.tcgetattr(descriptor)
    mode[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
    mode[6][termios.VMIN] = 1
    mode[6][termios.VTIME] = 0
    value = bytearray()
    pasting = False
    escape = bytearray()
    try:
        termios.tcsetattr(descriptor, termios.TCSANOW, mode)
        sys.stderr.write("\033[?2004h私钥或 PEM 文件路径（粘贴后按 Enter，Ctrl-C 取消）：")
        sys.stderr.flush()
        while True:
            character = os.read(descriptor, 1)
            if not character or character == b"\x04":
                raise EOFError("私钥输入已结束，请重新运行配置向导")
            if character == b"\x03":
                raise KeyboardInterrupt
            if escape or character == b"\x1b":
                escape.extend(character)
                if bytes(escape) in (b"\x1b[200~", b"\x1b[201~"):
                    pasting = bytes(escape) == b"\x1b[200~"
                    escape.clear()
                    if not pasting:
                        sys.stderr.write(f"\r已收到 {len(value)} 字节（内容隐藏），按 Enter 继续。")
                        sys.stderr.flush()
                elif not any(marker.startswith(escape) for marker in (b"\x1b[200~", b"\x1b[201~")):
                    escape.clear()
                continue
            if not pasting and character in (b"\x7f", b"\x08"):
                if value:
                    value.pop()
                continue
            if not pasting and character == b"\x15":
                value.clear()
                continue
            if not pasting and character in (b"\r", b"\n"):
                pem_incomplete = (
                    value.lstrip().startswith(b"-----BEGIN") and b"-----END" not in value
                )
                if not pem_incomplete:
                    break
            value.extend(character)
            if len(value) % 64 == 0:
                sys.stderr.write(f"\r已收到 {len(value)} 字节（内容隐藏），粘贴后按 Enter 继续。")
                sys.stderr.flush()
        return value.decode("utf-8").rstrip("\r\n")
    finally:
        termios.tcsetattr(descriptor, termios.TCSANOW, previous)
        sys.stderr.write("\033[?2004l\n")
        sys.stderr.flush()

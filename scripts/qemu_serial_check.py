#!/usr/bin/env python3
"""在有界时间内驱动 QEMU 串口，并按业务输出判定成败。

启动横幅本身不算通过。出现 panic / assertion、超时，或缺少要求的
业务输出时返回非 0。无论成败都把已读串口写入 --log。
QEMU 不能验证物理 PHY 或时序；调用方只应要求软件能在虚拟板上做出的行为。
"""

from __future__ import annotations

import argparse
import os
import re
import select
import signal
import subprocess
import time
from dataclasses import dataclass

_FORBIDDEN = (
    "ZEPHYR FATAL ERROR",
    "KERNEL PANIC",
    "ASSERTION FAIL",
    "***** USAGE FAULT",
)


@dataclass
class CheckResult:
    code: int
    reason: str


def execute(argv: list[str]) -> CheckResult:
    args = _parse(argv)
    log_path = args.log
    log_path.parent.mkdir(parents=True, exist_ok=True)
    master, slave = os.openpty()
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    proc = subprocess.Popen(
        args.command,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    buf = ""
    sent = 0
    deadline = time.monotonic() + args.timeout
    reason = "timeout waiting for required serial output"
    code = 1
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([master], [], [], min(0.1, remaining))
            if readable:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    buf += chunk.decode("utf-8", errors="replace")
                    _write_log(log_path, buf)
                    fatal = _forbidden(buf)
                    if fatal is not None:
                        code = 1
                        reason = f"fatal error in serial log: {fatal}"
                        break
                    sent = _send_ready(master, buf, args.ready, args.send, sent)
                    if _satisfied(buf, args.require, args.require_regex):
                        code = 0
                        reason = "required serial output observed"
                        break
                    continue
            if proc.poll() is not None:
                if _satisfied(buf, args.require, args.require_regex):
                    code = 0
                    reason = "required serial output observed"
                else:
                    code = 1
                    reason = "required serial output missing"
                break
    finally:
        _write_log(log_path, buf)
        _kill(proc)
        os.close(master)
    return CheckResult(code, reason)


def _parse(argv: list[str]) -> argparse.Namespace:
    if "--" not in argv:
        raise SystemExit("missing -- before the QEMU command")
    split = argv.index("--")
    parser = argparse.ArgumentParser(prog="qemu_serial_check")
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--log", type=_path, required=True)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--send", action="append", default=[])
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--require-regex", action="append", default=[])
    args = parser.parse_args(argv[:split])
    args.command = argv[split + 1 :]
    if not args.command:
        raise SystemExit("empty QEMU command")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    return args


def _path(value: str):
    from pathlib import Path

    return Path(value)


def _write_log(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _forbidden(text: str) -> str | None:
    for token in _FORBIDDEN:
        if token in text:
            return token
    return None


def _satisfied(text: str, literals: list[str], patterns: list[str]) -> bool:
    if not literals and not patterns:
        return False
    if any(item not in text for item in literals):
        return False
    return all(re.search(pattern, text) is not None for pattern in patterns)


def _send_ready(master: int, text: str, ready: str, commands: list[str], sent: int) -> int:
    seen = text.count(ready)
    while sent < len(commands) and seen > sent:
        os.write(master, (commands[sent] + "\n").encode("utf-8"))
        sent += 1
        # 本条命令回显后才会出现下一次提示符；避免同一提示符把队列一次发完。
        break
    return sent


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait(timeout=2)


def main(argv: list[str] | None = None) -> int:
    import sys

    result = execute(sys.argv[1:] if argv is None else argv)
    print(result.reason)
    return result.code


if __name__ == "__main__":
    raise SystemExit(main())

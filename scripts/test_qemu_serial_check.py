#!/usr/bin/env python3
"""qemu_serial_check 的主机侧用例：业务输出才算通过，超时和 panic 必须失败。"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qemu_serial_check  # noqa: E402


READY = "uart:~$"
REQUIRE = "传感器 1 阈值已更新: W=2.5 C=3.5 E=5.0"
REQUIRE_RE = r"Anomaly:\s+(INITIALIZED|RUNNING)"


def _argv(log_path: Path, timeout: str, program: str):
    return [
        "--timeout",
        timeout,
        "--log",
        str(log_path),
        "--ready",
        READY,
        "--send",
        "anomaly config 1 2.5 3.5 5.0",
        "--send",
        "gateway status",
        "--require",
        REQUIRE,
        "--require-regex",
        REQUIRE_RE,
        "--",
        sys.executable,
        "-c",
        program,
    ]


class QemuSerialCheckTest(unittest.TestCase):
    def test_timeout_fails_even_if_boot_banner_was_printed(self):
        program = textwrap.dedent(
            """
            import time
            print("*** Booting Zephyr OS build v4.4.0 ***", flush=True)
            print("Zephyr 工业边缘网关", flush=True)
            time.sleep(30)
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "serial.txt"
            result = qemu_serial_check.execute(_argv(log_path, "1", program))
            self.assertNotEqual(result.code, 0)
            self.assertIn("timeout", result.reason.lower())
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("Booting Zephyr", text)

    def test_boot_text_and_prompt_without_business_output_fails(self):
        program = textwrap.dedent(
            """
            print("*** Booting Zephyr OS build v4.4.0 ***", flush=True)
            print("Zephyr 工业边缘网关", flush=True)
            print("uart:~$ ", flush=True)
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "serial.txt"
            result = qemu_serial_check.execute(_argv(log_path, "2", program))
            self.assertNotEqual(result.code, 0)
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("Booting Zephyr", text)
            self.assertNotIn(REQUIRE, text)

    def test_panic_fails_even_when_required_line_is_present(self):
        program = textwrap.dedent(
            f"""
            import sys
            print("uart:~$ ", flush=True)
            sys.stdin.readline()
            print({REQUIRE!r}, flush=True)
            print("ZEPHYR FATAL ERROR 1: spurious", flush=True)
            sys.stdin.readline()
            print("Anomaly:   INITIALIZED", flush=True)
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "serial.txt"
            result = qemu_serial_check.execute(_argv(log_path, "2", program))
            self.assertNotEqual(result.code, 0)
            self.assertIn("fatal", result.reason.lower())

    def test_anomaly_threshold_and_status_pass(self):
        program = textwrap.dedent(
            f"""
            import sys
            print("*** Booting Zephyr OS build v4.4.0 ***", flush=True)
            print("uart:~$ ", flush=True)
            line = sys.stdin.readline()
            if "anomaly config" not in line:
                print("unexpected: " + line, flush=True)
                raise SystemExit(3)
            print({REQUIRE!r}, flush=True)
            print("uart:~$ ", flush=True)
            line = sys.stdin.readline()
            if "gateway status" not in line:
                print("unexpected: " + line, flush=True)
                raise SystemExit(4)
            print("Anomaly:   INITIALIZED", flush=True)
            print("uart:~$ ", flush=True)
            raise SystemExit(0)
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "serial.txt"
            result = qemu_serial_check.execute(_argv(log_path, "2", program))
            self.assertEqual(result.code, 0, result.reason)
            text = log_path.read_text(encoding="utf-8")
            self.assertIn(REQUIRE, text)
            self.assertIn("Anomaly:", text)


if __name__ == "__main__":
    unittest.main()

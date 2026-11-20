#!/usr/bin/env python3
"""Minimal adb device adapter for the leftover writeback PoC.

# Sanitized research copy. Ops/account/card/egress specifics removed.

The original workflow drove MuMu / Pixel desks through a much larger
in-house device module (screenshot OCR, Play focus detection, serial
lock files, frida-server supervision). Only the pieces the billing core
needs are reimplemented here with plain `adb`.

Configure with environment variables:
  MUMU_SERIAL   adb serial, e.g. 127.0.0.1:16384 (default) or a USB Pixel.
  GROK_CF_IP    optional --resolve pin for grok.com when DNS is blocked.
  OCR_CMD       optional shell command that reads a PNG on stdin and
                prints text (default: `tesseract stdin stdout -l eng`).
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

PKG = "ai.x.grok"
WORK = Path(os.environ.get("WORK_DIR", "./work")).resolve()
SERIAL = os.environ.get("MUMU_SERIAL", "127.0.0.1:16384")

WORK.mkdir(parents=True, exist_ok=True)


def adb(*args: str, timeout: float = 30.0) -> str:
    r = subprocess.run(
        ["adb", "-s", SERIAL, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return (r.stdout or "") + (r.stderr or "")


def shell(cmd: str, timeout: float = 30.0) -> str:
    return adb("shell", cmd, timeout=timeout)


def grok_pid() -> int | None:
    out = shell("pidof grok 2>/dev/null || pidof ai.x.grok 2>/dev/null").strip()
    if not out:
        return None
    try:
        return int(out.split()[0])
    except (ValueError, IndexError):
        return None


def ensure_frida_server() -> None:
    """Start /data/local/tmp/frida-server if pidof is empty.

    Real deployments keep a rooted MuMu/Pixel with frida-server pushed at
    this path; start it detached so fire_heavy.py can attach.
    """
    if shell("pidof frida-server 2>/dev/null").strip():
        return
    shell(
        "setsid /data/local/tmp/frida-server >/dev/null 2>&1 < /dev/null &"
    )
    time.sleep(1.5)


def screenshot(name: str = "shot.png") -> Path:
    dest = WORK / name
    shell(f"screencap -p /sdcard/{name}")
    adb("pull", f"/sdcard/{name}", str(dest))
    shell(f"rm -f /sdcard/{name}")
    return dest


def _ocr(png: Path) -> str:
    cmd = os.environ.get("OCR_CMD", "tesseract stdin stdout -l eng")
    try:
        r = subprocess.run(
            cmd, shell=True, input=png.read_bytes(),
            capture_output=True, timeout=40,
        )
        return r.stdout.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return f"ocr_error:{e}"


def screen_text(name: str = "shot.png") -> str:
    return _ocr(screenshot(name))


def ui_blob() -> str:
    """uiautomator dump; returns lowercased XML or ''."""
    shell("uiautomator dump /sdcard/ui.xml >/dev/null 2>&1")
    adb("pull", "/sdcard/ui.xml", str(WORK / "ui.xml"))
    shell("rm -f /sdcard/ui.xml")
    p = WORK / "ui.xml"
    if not p.is_file() or p.stat().st_size < 80:
        return ""
    return p.read_text("utf-8", "replace").lower()


def tap(x: int, y: int) -> None:
    shell(f"input tap {x} {y}")


def back(t: float = 0.6) -> None:
    shell("input keyevent 4")
    time.sleep(t)


def pkg_uid() -> int | None:
    out = shell(f"stat -c %u /data/data/{PKG} 2>/dev/null").strip()
    try:
        return int(out)
    except ValueError:
        return None


def run_as_app(cmds: list[str], uid: int, timeout: float = 35.0) -> str:
    """Run a script under the app uid (magisk su or root su on emulators)."""
    joined = " && ".join(cmds)
    return shell(f"su {uid} -c sh -c '{joined}'", timeout=timeout)


def push_run_app_script(local: Path, remote: str, uid: int) -> None:
    adb("push", str(local), "/data/local/tmp/_s.sh")
    shell(
        f"cp /data/local/tmp/_s.sh {remote} && chmod 755 {remote} "
        f"&& chown {uid}:{uid} {remote} && rm -f /data/local/tmp/_s.sh"
    )


def info() -> dict[str, Any]:
    return {"serial": SERIAL, "work": str(WORK), "pkg": PKG}

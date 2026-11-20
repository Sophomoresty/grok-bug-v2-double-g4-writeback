#!/usr/bin/env python3
"""Billing core for the leftover $0 writeback PoC (Grok / Google Play).

# Sanitized research copy. Ops/account/card/egress specifics removed.

Curated from the internal engine. Kept here, with original logic intact:

  * fire()/inspect()/ack_token()  - frida fire loop over heavy_wall.js
  * the wall gates               - TODAY_RE / FIRST_RE / gate_heavy / wait_wall
  * the xAI oracle               - get_subs() REST + device-uid gRPC,
                                   parse_subs_json(), leftover_ingest_ready()

Removed (ops): card picking / PAN freezer, wallet helpers, inventory and
handoff ledgers, serial lock files, proxy supervision. Call sites that
used them read plain env vars (CARD_PAN / SSO_JWT) or hit local stubs.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import device_min as device  # noqa: F401  (minimal adapter in this repo)

SCRIPTS = Path(__file__).resolve().parent
HOOKS = SCRIPTS.parent / "hooks"
FIRE = SCRIPTS / "fire_heavy.py"
ACK_TMPL = HOOKS / "ack_27008.js"
FRIDA_COMPILE = os.environ.get("FRIDA_COMPILE", "frida-compile")  # ops removed: pinned path
GROK_CF_IP = os.environ.get("GROK_CF_IP", "")  # ops removed: pinned edge IP

TODAY_RE = re.compile(r"starting today|today\s*\$|今天\s*\$", re.I)
FIRST_RE = re.compile(r"first charge will occur|将于.{0,40}收取", re.I)


def last_json(text: str) -> dict[str, Any]:
    """Last {...} or {...}... JSON object in a fire log line stream."""
    best: dict[str, Any] = {}
    for m in re.finditer(r"\{.*\}", text or "", re.S):
        try:
            v = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            best = v
    return best


def _run_fire(argv: list[str], extra: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run fire_heavy.py in its own process group so timeouts die with it."""
    env = dict(os.environ)
    env.update(extra)
    p = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(HOOKS), env=env, start_new_session=True,
    )
    try:
        stdout, stderr = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, p.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, 9)
        except ProcessLookupError:
            pass
        p.kill()
        try:
            stdout, stderr = p.communicate(timeout=3)
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""
        err = ((stderr or "") + "\n" + (stdout or "") + "\nfire_timeout")[-800:]
        return subprocess.CompletedProcess(argv, -9, stdout or "", err)


def fire(cmd: str, hook: str | None = None, timeout: int = 70, arg: str | None = None) -> dict[str, Any]:
    return _fire_run(cmd, hook=hook, timeout=timeout, arg=arg)


def _fire_run(cmd: str, hook: str | None = None, timeout: int = 70, arg: str | None = None) -> dict[str, Any]:
    device.ensure_frida_server()
    if not device.grok_pid():
        device.shell("am start -n ai.x.grok/.main.GrokActivity")
        time.sleep(2.0)
        if not device.grok_pid():
            return {"ok": False, "err": "grok_not_running"}
    extra: dict[str, str] = {}
    if hook:
        extra["HOOK_JS"] = hook
    argv = [sys.executable, str(FIRE), cmd]
    if arg:
        argv.append(arg)
    r = _run_fire(argv, extra, timeout)
    err = (r.stderr or "") + "\n" + (r.stdout or "")
    if r.returncode != 0 and ("timeout was reached" in err or "TransportError" in err):
        device.shell("am force-stop ai.x.grok")
        time.sleep(1.2)
        device.shell("am start -n ai.x.grok/.main.GrokActivity")
        time.sleep(3.0)
        r = _run_fire(argv, extra, timeout)
        err = (r.stderr or "") + "\n" + (r.stdout or "")
    box = last_json(err)
    box["_rc"] = r.returncode
    if r.returncode == -9 or "fire_timeout" in err:
        box.setdefault("ok", False)
        box.setdefault("err", "fire_timeout")
        box["cmd"] = cmd
        return box
    if r.returncode != 0 and box.get("ok") is not False:
        box.setdefault("ok", False)
        box.setdefault("error", err[-240:])
    return box


def compile_js(src: Path, dest: Path) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [FRIDA_COMPILE, str(src), "-o", str(dest)],
        capture_output=True, text=True, timeout=40, cwd=str(HOOKS), check=False,
    )
    ok = dest.exists() and dest.stat().st_size > 100 and r.returncode == 0
    return {"ok": ok, "src": str(src), "dest": str(dest), "stderr": (r.stderr or "")[-200:]}


# ---------------------------------------------------------------- inspect ---

def inspect(heavy_hook: str | None = None) -> dict[str, Any]:
    return fire("inspect", hook=heavy_hook)


def purchases(box: dict[str, Any]) -> list[dict[str, Any]]:
    return list(box.get("purchases") or [])


def find_sku(box: dict[str, Any], sku: str) -> dict[str, Any] | None:
    for p in purchases(box):
        if str(p.get("productId") or "") == sku:
            return p
    return None


# -------------------------------------------------------------------- ack ---

def ack_token(token: str, work: Path) -> dict[str, Any]:
    """Acknowledge the canceled grok.ultra (transact 902) before firing g4.

    g4 fired on an un-acked AR=false ultra returns "unable to change your
    subscription plan". After the ack the next fire lands a deferred wall
    and a new GPA.
    """
    if not token:
        return {"ok": False, "error": "empty_token"}
    src = work / "ack.fire.js"
    compiled = work / "ack.fire.compiled.js"
    text = ACK_TMPL.read_text(encoding="utf-8")
    text = re.sub(r"var TOKEN = '[^']*'", f"var TOKEN = '{token}'", text)
    src.write_text(text, encoding="utf-8")
    work.mkdir(parents=True, exist_ok=True)
    (work / "ack.token").write_text(token)
    comp = compile_js(src, compiled)
    if not comp.get("ok"):
        return {"ok": False, "error": "ack_compile", "compile": comp}
    box = fire("fire", hook=str(compiled))
    blob = json.dumps(box, default=str)
    acked = "acknowledged" in blob.lower() and "true" in blob.lower()
    if box.get("rc") in (0, "0") or box.get("ok") is True:
        acked = True
    if isinstance(box.get("after"), list) and box["after"]:
        acked = True
    box["acked"] = acked
    box["ok"] = bool(acked)
    return box


# ------------------------------------------------------------- wall gates ---

def wall_text(name: str = "wall.png") -> str:
    return device.screen_text(name)


def wall_is_ready(text: str) -> bool:
    """Subscribe wall is up. 'Starting Oct $299' without first-charge /
    Today $ copy is NOT ready - it is a full-price restore wall."""
    low = (text or "").lower()
    if not ("supergrok" in low or "grok ai" in low or "grok al" in low):
        return False
    if "starting oct" in low and ("299.99" in low or "$299" in low):
        if FIRST_RE.search(text or "") or "today $0" in low or "today $299" in low or "starting today" in low:
            return True
        return False
    return bool(
        "subscribe" in low or TODAY_RE.search(text or "") or FIRST_RE.search(text or "")
    )


def gate_heavy(text: str) -> dict[str, Any]:
    """Only a deferred wall (first charge later / today $0) may Subscribe."""
    low = (text or "").lower()
    has_first = bool(FIRST_RE.search(text))
    has_today = bool(TODAY_RE.search(text))
    if has_today:
        if "today $0" in low or "today $0.00" in low:
            return {"ok": True, "kind": "deferred", "text": text[:240]}
        if any(k in low for k in ("99.99", "299.99", "$99", "$299", "today $")):
            return {"ok": False, "kind": "CHARGE_FULL", "text": text[:240]}
    if has_first and not has_today:
        return {"ok": True, "kind": "deferred", "text": text[:240]}
    if "unable to change your subscription plan" in low:
        return {"ok": False, "kind": "unable_change", "text": text[:240]}
    return {"ok": False, "kind": "unknown", "text": text[:240]}


def wait_wall(timeout: float = 40.0) -> str:
    """Poll the wall until it is readable; returns OCR text (may be '')."""
    end = time.time() + timeout
    text = ""
    while time.time() < end:
        raw = device.ui_blob() or ""
        blob = raw.lower()
        if "no internet" in blob:
            time.sleep(2.0)
            continue
        if blob and ("subscribe" in blob or "supergrok" in blob or "billing" in blob):
            text = wall_text("wall.png")
            if "unable to change your subscription plan" in text.lower():
                return text
            if wall_is_ready(text):
                return text
        time.sleep(0.8)
    return wall_text("wall-late.png")


def tap_subscribe() -> bool:
    """Tap the Subscribe button. MuMu 1080p wall: cx,cy ~ (540, 1836)."""
    ocr = device.screen_text("tap-sub.png") or ""
    if "subscribe" in ocr.lower():
        device.tap(540, 1836)
        time.sleep(1.1)
        return True
    return False


def hold_requested() -> bool:
    """GROK_HOLD_SUBSCRIBE=1 -> screenshot the wall, let the human decide."""
    return os.environ.get("GROK_HOLD_SUBSCRIBE", "").strip().lower() in {"1", "true", "yes"}


# ----------------------------------------------------------------- oracle ---

def parse_subs_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad_json", "raw": (raw or "")[:400]}
    if isinstance(data, dict) and data.get("code") in (16, "16"):
        return {"ok": False, "error": "unauthenticated", "raw": (raw or "")[:400], "rows": []}
    rows = data if isinstance(data, list) else data.get("subscriptions") or data.get("items") or []
    if not isinstance(rows, list):
        rows = []
    ultra_active = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = str(
            row.get("product") or row.get("productId") or row.get("sku")
            or (row.get("google") or {}).get("productId") or row.get("tier") or ""
        )
        status = str(row.get("status") or "").upper()
        is_active = status.endswith("STATUS_ACTIVE") or status == "ACTIVE"
        if is_active and ("ultra" in sku.lower() or "HEAVY" in sku):
            ultra_active = row
    cap = None
    if ultra_active:
        cap = ultra_active.get("cancelAtPeriodEnd", ultra_active.get("cancel_at_period_end"))
    return {
        "ok": True,
        "rows": rows,
        "ultra_active": ultra_active,
        "cancelAtPeriodEnd": cap,
        "has_ultra_active": ultra_active is not None,
    }


def leftover_ingest_ready(got: dict[str, Any] | None) -> bool:
    """GET grok.ultra STATUS_ACTIVE **and** cancelAtPeriodEnd. Else never call
    it a success - Play can show a canceled row while xAI never attached."""
    box = got or {}
    return bool(box.get("has_ultra_active") and box.get("cancelAtPeriodEnd"))


def _get_subs_rest(jwt: str) -> str:
    """REST GET as the app uid, okhttp UA, optional --resolve pin."""
    uid = device.pkg_uid()
    if not uid:
        return ""
    resolve = f"--resolve grok.com:443:{GROK_CF_IP} " if GROK_CF_IP else ""
    script = (
        "#!/system/bin/sh\n"
        "curl -sS --max-time 20 "
        f"{resolve}"
        "-A okhttp/4.12.0 "
        f'-H "Cookie: sso={jwt}; sso-rw={jwt}" '
        '-H "Origin: https://grok.com" '
        '-H "Accept: application/json" '
        '"https://grok.com/rest/subscriptions?includeInactive=true"\n'
    )
    local = device.WORK / "get_subs.sh"
    local.write_text(script, encoding="utf-8")
    remote = "/data/data/ai.x.grok/cache/get_subs.sh"
    device.push_run_app_script(local, remote, uid)
    out = device.run_as_app([remote], uid, timeout=30)
    device.shell(f"rm -f {remote}")
    return out


def get_subs(jwt: str) -> dict[str, Any]:
    """xAI-side oracle. REST first; a Cloudflare HTML body is a miss, not a
    verdict - the gRPC variant lives in the full engine (ops removed here)."""
    raw = _get_subs_rest(jwt)
    stripped = (raw or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        box = parse_subs_json(raw)
        box["via"] = "device_uid"
        return box
    return {"ok": False, "error": "device_get_failed", "via": "device_uid", "raw": (raw or "")[:400]}

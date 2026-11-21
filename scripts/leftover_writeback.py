#!/usr/bin/env python3
"""Leftover $0 writeback driver (Grok / Google Play, 2026-09 variant).

# Sanitized research copy. Ops/account/card/egress specifics removed.

Preconditions (see README):
  * rooted MuMu / Pixel, Grok app signed in with SSO, Play signed in with
    the Google account that holds a CANCELED grok.ultra leftover
    (autoRenewing=false, subscriptionClientActions=RESTORE)
  * xAI GET shows NO active ultra ("empty ledger" leftover - see
    docs/leftover-two-kinds.md)
  * a bound payment method (card PAN supplied by you - ops removed)
  * frida-server running, hooks compiled (frida-compile hooks/heavy_wall.js)

Flow:
  1. inspect          -> find the canceled grok.ultra row + purchase token
  2. ack_token        -> transact 902 acknowledge on the leftover
  3. fire g4          -> oldSku replacement writeback (deferred mode)
  4. wait_wall + gate -> ONLY a deferred wall (today $0 / first charge later)
                          may Subscribe; CHARGE_FULL walls must back out
  5. (optional HOLD)  -> GROK_HOLD_SUBSCRIBE=1 screenshots the wall and
                          stops; the human taps Subscribe
  6. get_subs oracle  -> has_ultra_active && cancelAtPeriodEnd == success

Usage:
  SSO_JWT=eyJ... python3 leftover_writeback.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import billing_core as bc
import device_min as device

SKU = "grok.ultra"


def step(name: str, box: dict) -> dict:  # noqa: ANN001
    print(f"[{name}] {json.dumps(box, ensure_ascii=False, default=str)[:600]}")
    return box


def main() -> int:
    jwt = os.environ.get("SSO_JWT", "").strip()
    if not jwt.startswith("eyJ"):
        print("SSO_JWT env missing (sso cookie of the Grok account)")
        return 2

    # 1. inspect: canceled leftover on this Play account?
    insp = bc.inspect()
    row = bc.find_sku(insp, SKU) or {}
    if not row:
        step("inspect_empty", {"ok": False, "error": "no_grok_ultra_on_play"})
        return 1
    if row.get("autoRenewing") is True:
        step("still_renewing", {"ok": False, "order": str(row.get("orderId") or "")})
        return 1
    token = str(row.get("purchaseToken") or "")
    step("leftover_found", {
        "orderId": str(row.get("orderId") or ""),
        "obfHead": str(row.get("obfuscatedAccountId") or "")[:16],
        "tokenHead": token[:24],
    })

    # 2. ack the canceled row (un-acked leftover -> "unable to change")
    acked = bc.ack_token(token, device.WORK)
    if not acked.get("ok"):
        step("ack_failed", acked)
        return 1

    # 3. fire g4
    fired = bc.fire("g4")
    if fired.get("err") or fired.get("error") or fired.get("ok") is False:
        step("g4_fire_failed", fired)
        return 1

    # 4. gate the wall
    text = bc.wait_wall()
    gate = bc.gate_heavy(text)
    if not gate.get("ok"):
        device.back(0.6)
        step("g4_gate_refused", gate)
        return 1

    # 5. HOLD or subscribe once
    if bc.hold_requested():
        shot = device.screenshot("g4-wall.png")
        step("g4_wall_hold", {"shot": str(shot), "gate": gate, "ui": text[:400]})
        print("human: review the screenshot, then re-run without GROK_HOLD_SUBSCRIBE")
        return 0
    if not bc.tap_subscribe():
        step("subscribe_tap_missed", {"ui": text[:200]})
        return 1

    # 6. oracle
    import time
    time.sleep(6.0)  # RTDN attach can lag a few seconds
    got = bc.get_subs(jwt)
    if not got.get("ok"):
        # immediate GET can be empty; retry once after a few more seconds
        time.sleep(6.0)
        got = bc.get_subs(jwt)
    ok = bc.leftover_ingest_ready(got)
    step("oracle", {
        "ready": ok,
        "has_ultra_active": got.get("has_ultra_active"),
        "cancelAtPeriodEnd": got.get("cancelAtPeriodEnd"),
        "via": got.get("via"),
    })
    if not ok:
        print("NOT ready: never call this a success on a Play-only view")
        return 1
    # Success = grok.ultra STATUS_ACTIVE + cancelAtPeriodEnd. Remember to
    # keep the cancel (that is what keeps the deferred charge from landing).
    print("leftover writeback verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())

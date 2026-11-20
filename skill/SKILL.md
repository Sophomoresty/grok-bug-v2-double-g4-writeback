---
name: grok-play-leftover-writeback
owner: sophomores
description: "Use when reproducing the 2026-09 Grok leftover $0 writeback (canceled grok.ultra -> ACTIVE via g4). 当要复现空账本 leftover 写回时使用."
---

# grok-play-leftover-writeback (research copy)

## Overview

- 输入: Play 上 `autoRenewing=false` 的 `grok.ultra` leftover + 空的 xAI 账本 (`sync updated:0` 拉不回).
- 输出: xAI GET `grok.ultra STATUS_ACTIVE` + `cancelAtPeriodEnd`, 全程 $0, 不买 Lite.
- 核: `scripts/leftover_writeback.py` 六步: inspect → ack(902) → g4 → 墙闸 → HOLD/Subscribe → oracle.

## Always Read

1. `docs/leftover-two-kinds.md` (先判别 leftover 类型)
2. `README.md` 机制节

## Common Tasks

- 判别 leftover 类型 → `docs/leftover-two-kinds.md`
- 找 leftover token / obfuscatedAccountId → `scripts/fire_heavy.py inspect`
- 补 ack → `billing_core.ack_token`
- fire g4 / 看墙 → `billing_core.fire("g4")` + `gate_heavy`
- 验证 → `billing_core.get_subs(SSO_JWT)` + `leftover_ingest_ready`

## Known Gotchas

- 未 ack 的 leftover 直接 g4 = "unable to change your subscription plan".
- 墙是 `CHARGE_FULL` (today $99/$299) 一律 BACK, 不 Subscribe, 不 Pause.
- REST 被 Cloudflare 挡不是终态结论, 换设备 uid gRPC 再判.
- leftover 上烙别人 GID → 本仓库路径不适用, 走 hop (grok-play-leftover-hop).
- GET 已 ACTIVE 禁止再 POST sync, 禁止 g4 自替换.

## References

- `scripts/` `hooks/` `docs/` 同仓库

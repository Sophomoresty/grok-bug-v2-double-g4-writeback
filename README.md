# grok-bug-v2-double-g4-writeback

Google Play 已停续 `grok.ultra` leftover 的 **两次 `g(4)` 零元写回** 复现 (Grok / SuperGrok Heavy, 2026-09 变体).

一句话: 第一次 `g(4)` (`fromlite`) 掉单后, Play 上躺着一张 `autoRenewing=false` 的 `grok.ultra` 停续单, xAI 侧账本是空的; 把这张单当 `oldSkuPurchaseToken` 发起**第二次 `g(4)`** 自替换写回, 首期 deferred (today $0), Subscribe 一次后 xAI GET 变 `STATUS_ACTIVE + cancelAtPeriodEnd` — 全程不用再买 Lite.

配套技术稿: 见 `POST.md`.
- v1 (`grok-bug-v1-deferred-upgrade`): 一次 `g(4)` Lite 一付三 deferred replacement (2026-08)
- v1-番外 (`grok-bug-v1-extra-delayed-sync`): v1 失效后的延迟首次 sync 番外
- v3 (`grok-bug-v3-account-hop`): 基于 v2 的 leftover A→B 跨账号转移 (hop)
- v4 (`grok-bug-v4-x-subscription-ultra`): `x.subscription.ultra` 新版重建 (2026-10)

## 机制

1. `inspect`: e2 (getPurchases) 找到 leftover 行 + `purchaseToken`
2. `ack_token`: transact 902 补 acknowledge — 不 ack 直接 g4 会 "unable to change your subscription plan"
3. `g4`: d2 (getBuyIntent) extras 带 `oldSkuPurchaseToken` + `skusToReplace` + `replacementMode` + `subscriptionProductReplacementParamsList` (play_billing t1/m1/o1 proto), `accountId` 用收据上的 `obfuscatedAccountId`
4. 墙判定: 只有 deferred (first charge later / today $0) 才准 Subscribe; `CHARGE_FULL` (today $99/$299) 一律 BACK
5. oracle: 校验 `GET /rest/subscriptions?includeInactive=true`, `has_ultra_active && cancelAtPeriodEnd` 才算成 (若 REST 被 Cloudflare 拦截, 可改走设备 uid gRPC, 具体 `curl` 命令见 `POST.md`)

## 目录

```
hooks/package.json        # frida-java-bridge 依赖声明 (编译前需 npm install)
hooks/heavy_wall.js       # Frida hook: e2/g2/d2 + g4/skipg4/destg4 导出 (需 frida-compile)
hooks/ack_27008.js        # transact 902 acknowledge 模板 (TOKEN 运行时替换)
hooks/dump_gid.js         # vending dump obfuscatedAccountId
scripts/fire_heavy.py     # frida attach + rpc fire dispatcher
scripts/billing_core.py   # fire 循环 / ack / 墙闸 / xAI oracle
scripts/device_min.py     # 最小 adb 适配 (截图 OCR / uiautomator / su)
scripts/leftover_writeback.py  # 写回驱动 (六步)
docs/leftover-two-kinds.md     # 交付型停续 vs 空账本 leftover 判别
skill/SKILL.md            # 写回流程操作清单
POST.md                   # 技术稿
```

## 使用 (研究环境自备)

```bash
pip install frida-tools
(cd hooks && npm install)
frida-compile hooks/heavy_wall.js -o hooks/heavy_wall.compiled.js
# rooted MuMu/Pixel: frida-server 已跑, Grok 已 SSO 登录, Play 是持有 leftover 的 Google
export MUMU_SERIAL=127.0.0.1:16384
export SSO_JWT=eyJ...            # sso cookie
export GROK_HOLD_SUBSCRIBE=1     # 有人值守: 墙出来先截图, 人确认再点
python3 scripts/leftover_writeback.py
```

## 免责声明

- 仅供 Google Play Billing 计费机制研究 / 逆向学习.
- 本仓库为脱敏研究副本: 不含任何账号, 支付卡, 代理出口信息; `docs` 里的时间线是机制研究记录.
- 不要用它对非自有账号做任何事; 使用产生的后果自负.

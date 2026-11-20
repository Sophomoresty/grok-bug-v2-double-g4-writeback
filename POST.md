# 不买 Lite 也写回: Google Play 停续 leftover 的 $0 复活

上一篇 (8 月的那次) 复现 "一付三" 还有个前置成本: 先花 ~$10 买一个月 grok.lite 当替换跳板. 9 月初在盘市面掉单号的时候发现一件更有意思的事 — 有些号 Play 里躺着一张**已经停续**的 grok.ultra 单, xAI 侧却什么都没有. 这张 "尸体" 本身就能当跳板, 一分钱不用再花. 这篇讲怎么把它写回去. 文里略去了具体账号和卡信息.

## 0x00 背景: sync 拉不回来的单

典型样本: Play 订阅页有 `grok.ultra`, `autoRenewing=false`, 详情页按钮是 Restore; 但 xAI 侧 `GET /rest/subscriptions` 是空的, 手动 `POST /rest/subscriptions/sync` 回 `updated:0` — 服务端不认这张单的归属.

为什么: 这种单是卖家在自己设备上写回后又转手/弃号的产物, `obfuscatedAccountId` (xAI 用来认领的 64-hex GID) 烙的还是**当时那台设备对应账号**的 GID. 账号一换, xAI 侧自然认不到.

先分清两种 "leftover", 处置完全不同:

| 类型 | xAI GET | Play | 处置 |
|---|---|---|---|
| 交付型停续 | 已有 ACTIVE ultra + cap | autoRenewing=false | 终态, 别碰 |
| 空账本 leftover | 无 ACTIVE | 有停续 grok.ultra (RESTORE) | 本文: 可写回 |

## 0x01 思路: 尸体当 oldSku

一付三的本质是 "拿一张已有订阅发起 deferred replacement". 那张已有订阅**不需要还活着** — 停续单的 `purchaseToken` 依然在 Play 账本里, 一样能填进 `oldSkuPurchaseToken` 发起替换. 替换出来的新单是全新订阅, 首扣 deferred, 而 oldSku 已经停续不会续费 — 结果就是 today $0.

## 0x02 链路

六步, 每步都有闸:

1. **inspect**: e2 `getPurchases` 找到 leftover 行, 拿 `purchaseToken` 和收据上的 `obfuscatedAccountId`
2. **ack**: 对这张 un-acked 的停续单 transact 902 补确认. 不做这步直接 fire, 墙上就是一句 "unable to change your subscription plan"
3. **fire g4**: d2 extras 带上 leftover 的 token 做 oldSku, `accountId` 用收据上的 obf (不是你自以为的 GID)
4. **判墙**: 只有 deferred 墙准 Subscribe:

```python
TODAY_RE = re.compile(r"starting today|today\s*\$", re.I)
FIRST_RE = re.compile(r"first charge will occur|将于.{0,40}收取", re.I)

def gate_heavy(text):
    low = text.lower()
    if TODAY_RE.search(text):
        if "today $0" in low:
            return {"ok": True, "kind": "deferred"}
        if any(k in low for k in ("99.99", "299.99", "$99", "$299", "today $")):
            return {"ok": False, "kind": "CHARGE_FULL"}   # 一律 BACK
    if FIRST_RE.search(text) and not TODAY_RE.search(text):
        return {"ok": True, "kind": "deferred"}           # 首扣在未来 = 今天 $0
    return {"ok": False, "kind": "unknown"}
```

5. **Subscribe 一次**: 有人值守, 截图确认墙上没有 Today 全款再点. 点完立刻停续 (cancelAtPeriodEnd), deferred 的那笔周期末扣款就永远不会发生
6. **oracle**: xAI GET `has_ultra_active && cancelAtPeriodEnd` 同时成立才算成. 只看 Play 界面说 "Subscribed" 不算

## 0x03 踩坑

- **REST 被 Cloudflare 挡**: 浏览器/主机出口打 `/rest/subscriptions` 常回 Just a moment HTML. 不是终态结论 — 把 curl 塞进 app uid 用 okhttp UA 发, 或干脆走设备 uid gRPC `GetSubscriptions` (空请求体), 同一 JWT 两条路结果一致
- **obfuscatedAccountId 是空的**: 收据没有 obf 时 g4 不 fire, 这是保护 — 空 obf 的单子写回去 xAI 认不了主, 是 hop 篇的课题
- **already owned / STORE_PROOF_ALREADY_BOUND**: Play 库里已有活单时, 换 Google 再来, 别对着同一只硬 Subscribe
- **CHARGE_FULL 墙**: 出现 "Starting today $99.99/$299.99" 说明 Play 把它当全新首购了 (oldSku 没吃进去), BACK 出来查 inspect, 别付款
- **写回成功后别再 sync**: GET 已 ACTIVE 时 POST sync 是事故操作

## 0x04 成本账

一付三: ~$10 (一个月 Lite 实扣) + 一张卡.
leftover 写回: $0, 不买任何东西, 只需要手上有带停续单的 Play 账号 + 一张绑定的支付方式 (Play 要求墙上有卡才出 deferred).

## 0x05 工具

脱敏代码在 GitHub: `grok-play-leftover-writeback` (private, 审完后公开, 链接补在这).
六步驱动 `leftover_writeback.py` + hook `heavy_wall.js` (g4/skipg4/destg4 导出都在里面).

下一篇 (hop): leftover 上烙着别人 GID 时, 怎么把它洗掉再烙上买家的 — 让货可以在账号间搬家.

仅供 Play Billing 计费机制研究, 请勿用于非自有账号.

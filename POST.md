# 不买 Lite 也写回： Google Play 停续 leftover 的 $0 复活实录

上一篇（8 月那次）复现 "一付三" 有个前置成本：先花 ~$10 买一个月 grok.lite 当替换跳板。9 月初盘库存的时候，一批 8 月的掉单号引起了注意——xAI 侧全是墓碑，Play 里却各自躺着一张**已经停续**的 grok.ultra。把这张 "尸体" 当 oldSku 再走一次替换，写回当天实扣 $0。这篇把整条链路和所有死法记一遍。文里略去了具体账号和卡信息。

## 0x00 起点：二十只墓碑号里躺着什么

8 月中旬那批 ~20 只号，GET `includeInactive` 全是 INACTIVE，当时定性 "无法恢复"。9 月初换个顺序复查——先活 GET + `GetGoogleObfuscatedId`（看 existingActive），再看 Play inspect——三只样本当场分成三类：

| 号 | xAI GET | Play 库 | 定性 |
|---|---|---|---|
| 内部号G | grok.ultra ACTIVE，cap=true，token `<24字符小写>...` | 同 token，autoRenewing=false | **交付 leftover**：已经写回过的终态，禁止再动 |
| 产线号I | grok.ultra ACTIVE 至 10-02，cap=true | 同 token leftover | 同上（产线 lite→g4 写回号） |
| 复刻号D | `includeInactive` 是 **[]**（空） | 有 grok.ultra，obf=该号 GID，Next $299.99 Sep 29，clientActions=CANCEL | **空账本 leftover**：可写回的原料 |

共同字段形状：orderId 是 GPA.XXXX-XXXX-XXXX-XXXXX（4-4-4-5），purchaseToken 24 字符小写，obfuscatedAccountId 64-hex，subscriptionClientActions 是 RESTORE 或 CANCEL。

## 0x01 sync 拉不回来：亲手把这条路堵死

空账本第一反应是 REST sync。宿主直接 curl grok.com 被 Cloudflare 403（返回体是 challenge HTML 不是 JSON），于是把请求塞进设备上的 Grok 进程：CookieManager.setCookie(sso=jwt) + WebView 过一遍 grok.com 拿到会话，再进程内 HttpURLConnection 打 REST：

```
GET  /rest/subscriptions            → []            (200)
POST /rest/subscriptions/sync       → {"skipped":false,"updated":0,"errors":0}   (200)
GET  /rest/subscriptions            → []            (还是空)
```

换着花样打：带 purchaseToken 字段回 `skipped:true`；`{"hard":true}` 一样 `updated:0`；后来 v4 时代还确认了 `/rest/subscriptions/{cancel,resume,change,upgrade,restore,reactivate,switch,purchases}` 全 404。结论：**sync 是服务端主动拉取，没有客户端信任面**，xAI 没存这只号的 ultra token，伪造任何字段都没意义。空账本要活，只能让 Play 再发一次真实 RTDN——也就是 leftover 再走一次 g(4)。

## 0x02 尸体能当 oldSku 的推理

上一篇的引擎是 WITHOUT_PRORATION 替换：新单首扣推迟到 oldSku 周期末。关键观察：**oldSku 不需要还活着**。停续单的 purchaseToken 还在 Play 账本里，照样填进 `oldSkuPurchaseToken`；oldSku 已停续不会续费，替换出来的新单首扣又 deferred——叠加结果就是今天 $0。区别只在：这次 old 和 new 都是 `grok.ultra`（自替换），accountId 用 leftover 收据上的 obf。

## 0x03 链路：六步，每步都有闸

**① inspect**：e2（getPurchases）读 leftover 行，拿 token 和收据 obf：

```json
{"productId":"grok.ultra","orderId":"GPA.XXXX-...","autoRenewing":false,
 "acknowledged":false,"obfuscatedAccountId":"<64-hex>","purchaseToken":"<24字符>"}
```

**② ack**：un-acked 的停续单直接替换会撞 "unable to change"，先 transact 902 补确认：

```js
data.writeInterfaceToken('com.android.vending.billing.IInAppBillingService');
data.writeInt(9);                  // apiVersion
data.writeString('ai.x.grok');
data.writeString(token);           // leftover 的 purchaseToken
data.writeInt(1);
extra.writeToParcel(data, 0);      // Bundle{playBillingLibraryVersion:'9.1.1', enablePendingPurchases}
raw.transact(902, data, reply, 0); // reply.readException() 无异常 = acknowledged
```

**③ fire g4**：d2 extras = oldSkuPurchaseToken(leftover token) + skusToReplace=['grok.ultra'] + replacementMode=3 + proto（old=new=ultra，lb.g(4)）+ accountId=收据 obf。

**④ 判墙**，只认 deferred 原文：

```
SuperGrok Heavy
Starting Sep 30 $99.99/month
Your first charge will occur on Sep 30
（没有任何 Starting today / Today $ 行）
```

毒墙长这样，一律 BACK 不订：

```
SuperGrok Heavy  Starting today $99.99/month then $299.99/month        ← CHARGE_FULL
Today $99.99 + "57 days (includes 27 days converted from the remaining
time on your current plan)" + $99.99/month from Oct 30 ...              ← g(6) 的换算墙
```

判定写成正则而不是肉眼：`TODAY_RE = starting today|today\s*\$`，`FIRST_RE = first charge will occur|将于.{0,40}收取`；有 Today 且不是 $0 → CHARGE_FULL；只有 first-charge 在未来 → deferred。后来 hop 时代还吃过一次亏：OCR 把 "Your first charge..." 那行截断了，一张好好的 deferred 墙被分类成 CHARGE_FULL——所以判定要等 OCR 完整或截图人工过一眼。

**⑤ Subscribe 一次 + 立刻停续**：deferred 的周期末扣款靠 cancelAtPeriodEnd 躲掉。停续点右边 Cancel（坐标特征 786,1857），**绝不点左边的 Pause payments**——OCR 会把这俩连成一个串，点错就翻车。

**⑥ oracle**：GET 出 `grok.ultra ACTIVE + cancelAtPeriodEnd` 才算成。写回成功的典型形态：1 条 ACTIVE（新 token）+ 1 条同号 ultra INACTIVE（旧 token，expiry 被裁到 create 秒），0 lite。

## 0x04 死法大全（每条都真撞过）

- **autoRenewing 的 ultra 自替换 → unable**：替换前必须先 Cancel 成 leftover（autoRenewing=false），活的单不给换。
- **空库幻想**：空 Play 上不发 oldSku 只带 replacementMode=3（firstg4extras）→ d2rc=0，墙 "Starting today $99.99/month then $299.99 (Dec 2)"——**Play 没有 old sku 时直接丢掉 replacement extras 按全新首购计价**。空库 g2 三相复查也确认目录里没有 micros=0 的 offer：$0 只存在于 oldSku 替换，不存在于任何 "隐藏试用档"。
- **RESTORE 型的 offer 坑**：actions=RESTORE 的 leftover，先试 p1m offer 撞 unable，改 p3m 就出无 Today 的 $0 墙。两个变体都保留在脚本里（skipg4 / skipg4p1m），按现场顺序试。
- **收据 obf 与账号 GID 不一致**：墙回 "item you requested is not available"。教训写进了脚本注释：leftover g4 永远用**收据上的** obf，不是你以为的当前号 GID。
- **REST 被 Cloudflare 挡**：宿主出口打 REST 403 challenge HTML。第一个绕法是设备进程内 okhttp UA + `--resolve` 钉 IP，时灵时不灵。稳定解是**设备 uid 直发 gRPC**：

```bash
# 5 字节空 gRPC frame
printf '\x00\x00\x00\x00\x00' > grpc-empty.bin
curl -sS --max-time 25 --http2 --resolve grok.com:443:<edge-ip> \
  -A okhttp/4.12.0 \
  -H "Cookie: sso=$JWT; sso-rw=$JWT" -H "Content-Type: application/grpc" \
  -H "TE: trailers" -H "Origin: https://grok.com" \
  --data-binary @grpc-empty.bin -D - -o body.bin \
  "https://grok.com/grok_api_v2.Subscriptions/GetSubscriptions"
```

以 app uid 执行（Pixel 走 `su <uid> -c`，MuMu 直接 root chown）。同一个钉定 IP 上 REST 是 "Just a moment"，device uid gRPC 老老实实返回 `has_ultra_active + cancelAtPeriodEnd`。

- **GET 立刻为空 ≠ 失败**：Subscribe 后 RTDN 要飞一会儿。干净谱系实测 play_to_xai 57~375 秒；模拟器+DC IP+虚拟卡的风控谱系会 hold——Lite 那笔被 hold 过 2h05m，$0 deferred 单甚至 16h+ 未落（Play 有 "Confirm plan before X" 硬期限，过期自动取消）。所以等 5-10 分钟再判，别手贱重跑。
- **已 ACTIVE 后手贱**：GET 已 ACTIVE 时再 POST sync 是事故操作——sync 会先关旧行造成账本空窗。同理禁止再 g4 自替换。

## 0x05 战果与成本的真相

- 09-02：两只端到端。内部号E（canceled ultra 自替换 g(4)）today $0 → GET ACTIVE；内部号F（Pixel 形）空库 Lite $9.99 → fromlite 升档 → xAI drop → Play cancel 成 leftover → g(4) 写回 → GET ACTIVE。
- 09-03：复刻号D 打通（终态 1 ACTIVE + 1 INACTIVE 残，0 lite）。
- 09-04：**同一只 leftover 当天连跳四只 dest，全天 $0**——这也是下一篇 hop 的开场。
- 库存盘点（43 只）：ACTIVE 15 / 墓碑 28（其中已售 18）；8 月那批 ~20 只里没有 Play leftover 的，确实写不回去。

成本要说清楚：leftover 写回**当天**是 $0（两堵墙首扣都在周期末，停续锁死），但如果 leftover 是自己造的，前置还有一笔 ~$10 的 Lite。真正的 "总计 $0" 只有拿到现成 leftover（比如外号弃单）时的洗写路径——那是下一篇的事。

## 0x06 工具

脱敏代码在 GitHub：`grok-play-leftover-writeback`（private，审完后公开，链接补在这）。
六步驱动 `leftover_writeback.py` + hook `heavy_wall.js`（g4/skipg4/destg4 导出都在里面）+ 判别文档 `docs/leftover-two-kinds.md`。

_文里略去了具体账号和卡信息。仅供 Play Billing 计费机制研究，请勿用于非自有账号。_

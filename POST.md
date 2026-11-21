# 两次 g4 零元写回: 利用 Play 停续旧单恢复掉单的 Grok Heavy

v1 (一次 `g(4)`) 与 v1-番外 (延迟首次 sync) 都要求在未提前同步 `lite` 的前提下完成升级. 但实际排查中, 大量账号已经先同步了 `grok.lite`, 导致第一次 `g(4)` (`fromlite`) 在 xAI 侧掉单: 9 月初复查 8 月中旬遗留的约 20 只掉单账号时发现, 这些账号在 xAI 侧查询均无有效订阅, 但对应的 Google Play 购买库中仍保留着那张第一次 `g(4)` 生成的, **已取消续订** 的 `grok.ultra` 订单.

以这张已停续的 `grok.ultra` 旧单作为 `oldSku`, 在 Play 侧发起**第二次 `g(4)`** 同 SKU 自替换 (`grok.ultra` -> `grok.ultra`), 写回当天实扣 `$0` 即可生成新的 Play 订单与 RTDN, 重新触发 xAI 建行 `ACTIVE`. 本文记录库存盘点分类, 同步接口验证, 两次 `g(4)` 六步写回链路以及调试过程中遇到的拦截情况.

## 0x00 库存复查与两类停续订单的区分

8 月批次的掉单号在早先排查时仅检查了 `/rest/subscriptions?includeInactive=true`, 因全部显示 `INACTIVE` 而被判定为不可恢复. 9 月初调整排查顺序, 先调用活体 `GetSubscriptions` 与 `GetGoogleObfuscatedId`, 再结合模拟器检查 Google Play 本地购买库 (`e2`), 样本账号立即分化为三种状态:

| 样本 | xAI 侧查询结果 | Play 本地库记录 | 状态判定 |
|---|---|---|---|
| 样本 G | `grok.ultra` ACTIVE, `cancelAtPeriodEnd=true` | 同 token, `autoRenewing=false` | 已完成写回的交付终态, 禁止再次操作 |
| 样本 I | `grok.ultra` ACTIVE 至 10-02, `cancelAtPeriodEnd=true` | 同 token 停续单 | 前期 Lite 升档写回的成品 |
| 样本 D | `includeInactive` 返回 `[]` (空) | 存在 `grok.ultra`, 绑定该号 GID, 下期 $299.99 Sep 29, `CANCEL` | xAI 空账本, Play 账本留存, 可执行写回 |

Play 侧留存记录的字段格式一致: `orderId` 为 `GPA.XXXX-XXXX-XXXX-XXXXX` (4-4-4-5 结构), `purchaseToken` 为 24 位小写字母串, `obfuscatedAccountId` 为 64 位 hex, `subscriptionClientActions` 为 `RESTORE` 或 `CANCEL`.

需要严格区分两类停续旧单: 若 xAI 侧已存在 `ACTIVE` 且 `cancelAtPeriodEnd=true` 的订阅, 说明该单已完成交付并关闭续订, 再次发起替换会破坏现有状态; 只有 xAI 侧无有效行 (空或仅有历史失效记录), 且 Play 侧保留绑定同一 GID 的停续 `grok.ultra` 时, 才属于可写回的原料.

## 0x01 REST sync 同步验证

面对 Play 有单而 xAI 空账本的情况, 首选测试路径是调用 `POST /rest/subscriptions/sync` 触发同步.

从宿主机直接向 `grok.com` 发起 REST 请求会被 Cloudflare 拦截返回 403 challenge HTML, 因此需要将请求注入模拟器内的 Grok 进程执行: 通过 `CookieManager` 写入 `sso` cookie 并用 WebView 访问 `grok.com` 获取会话后, 在进程内调用 `HttpURLConnection` 发送请求:

```text
GET  /rest/subscriptions            -> []                                           (200)
POST /rest/subscriptions/sync       -> {"skipped":false,"updated":0,"errors":0}     (200)
GET  /rest/subscriptions            -> []                                           (仍为空)
```

进一步测试不同参数组合: 请求体携带 `purchaseToken` 字段返回 `skipped:true`; 传入 `{"hard":true}` 依然返回 `updated:0`. 后续遍历 `/rest/subscriptions/{cancel,resume,change,upgrade,restore,reactivate,switch,purchases}` 等猜想路径, 均返回 404.

实验结果说明 `/rest/subscriptions/sync` 完全由服务端根据已有记录主动拉取状态, 不接受客户端传入的未记录凭证. 当 xAI 账本尚未保存 `ultra` token 时, 只能通过 Google Play 重新触发一次真实 RTDN 通知来恢复.

## 0x02 停续旧单自替换的计费条件

`WITHOUT_PRORATION` (`g(4)`) 模式会将新订阅的首期扣款日推迟到 `oldSku` 的当前周期末. 该机制能够复用停续旧单的关键在于: Google Play 并不要求 `oldSku` 处于 `autoRenewing=true` 状态.

只要该订单仍处于当前周期内, 即使已取消续订 (`autoRenewing=false`), 其 `purchaseToken` 仍然保留在 Play 购买库中. 将其填入 `oldSkuPurchaseToken`, 新旧 SKU 均设为 `grok.ultra` (同商品自替换), `accountId` 保持原收据的 `obfuscatedAccountId`. 由于旧单不再续费且新单首扣被推迟到旧单周期末, 当天实际扣款为 `$0`, 同时生成新的 Play 订单并下发 RTDN 至 xAI.

## 0x03 六步写回链路与校验闸门

完整的写回流程由六个步骤组成, 每一步均设置前置校验:

**1. 读取购买库 (`inspect`)**: 调用 `e2` (`getPurchases`) 提取停续 `grok.ultra` 的 `purchaseToken`, `obfuscatedAccountId` 与 `acknowledged` 字段:

```json
{
  "productId": "grok.ultra",
  "orderId": "GPA.XXXX-XXXX-XXXX-XXXXX",
  "autoRenewing": false,
  "acknowledged": false,
  "obfuscatedAccountId": "<64-hex>",
  "purchaseToken": "<24位小写字符>"
}
```

**2. 补发确认 (`ack`)**: 未确认的停续订单直接替换会返回 `"unable to change"`, 必须先通过 `transact(902)` 完成确认:

```js
data.writeInterfaceToken('com.android.vending.billing.IInAppBillingService');
data.writeInt(9);                  // apiVersion
data.writeString('ai.x.grok');
data.writeString(token);           // leftover 的 purchaseToken
data.writeInt(1);
extra.writeToParcel(data, 0);      // Bundle{playBillingLibraryVersion:'9.1.1', enablePendingPurchases}
raw.transact(902, data, reply, 0); // 无异常即 acknowledged=true
```

**3. 发起替换 (`fire g4`)**: 调用 `d2` 传入 `oldSkuPurchaseToken` (旧单 token), `skusToReplace=['grok.ultra']`, `replacementMode=3`, 自替换 proto (`old=new=grok.ultra`, `l1.g(4)` 对应 `zzg=3`), 以及旧单收据上的 `obfuscatedAccountId`.

**4. 支付墙正则校验**: 仅当支付弹窗完全匹配纯延期文案时允许点击 Subscribe:

```text
SuperGrok Heavy
Starting Sep 30 $99.99/month
Your first charge will occur on Sep 30
(禁止出现任何 Starting today 或 Today $ 行)
```

若出现以下两类立即扣款文案, 一律返回退出:

```text
SuperGrok Heavy  Starting today $99.99/month then $299.99/month
(或者 CHARGE_FULL 换算墙)
Today $99.99 + "57 days (includes 27 days converted from the remaining time on your current plan)" + $99.99/month from Oct 30 ...
```

脚本中使用正则 `starting today|today\s*\$` 与 `first charge will occur|将于.{0,40}收取` 进行分类. 曾出现 OCR 截断底部 `"Your first charge..."` 行导致延期墙被误判为全款墙的情况, 因此必须确保 OCR 文本完整或经过截图确认.

**5. 订阅与停续**: 点击一次 Subscribe 后, 立即进入 Play 订阅详情页取消续订. 需注意详情页底部左侧按钮为 `"Pause payments"`, 右侧为 `"Cancel"` (坐标特征 `786,1857`), OCR 常将两者识别为连续字符串, 必须精确点击右侧按钮.

**6. xAI 侧状态验收**: 查询 `GetSubscriptions` 直到返回 `grok.ultra ACTIVE` 且 `cancelAtPeriodEnd=true`. 写回成功后的典型数据形态为: 1 条新 token 的 `ACTIVE` 记录, 1 条同账号旧 token 的 `INACTIVE` 记录 (过期时间被裁剪至建行秒), 以及 0 条 `grok.lite`.

## 0x04 异常场景与网络绕过方案

实测过程中遇到了以下几类典型限制与故障:

- **未停续直接自替换**: 对 `autoRenewing=true` 的 `grok.ultra` 直接发起 `g(4)` 会触发 `unable` 拒绝, 必须先 Cancel 成停续状态.
- **空库注入无 `oldSku`**: 在空 Play 库上不传 `oldSkuPurchaseToken` 仅带 `replacementMode=3` 调用 `d2`, 虽返回 0 但弹窗仍显示 `"Starting today $99.99/month then $299.99"`. 缺乏 `oldSku` 时 Play 服务端会丢弃替换参数并按全新首购计价. 对空库下 `g2` 商品目录的三项复查也确认目录内不存在 `micros=0` 的隐藏档位.
- **`RESTORE` 状态的 offer 兼容性**: 当 `subscriptionClientActions` 为 `RESTORE` 时, 部分场景下传入 `p1m` offer 会报 `unable`, 切换为 `p3m` offer 后可正常弹出 `$0` 延期墙, 脚本中保留了两种变体按顺序尝试.
- **`accountId` 必须取自收据**: 若传入的 `accountId` 与旧收据上的 `obfuscatedAccountId` 不一致, 弹窗会提示 `"The item you requested is not available"`. 即使重新计算当前号 GID, 自替换也必须沿用收据原值.
- **REST 403 与设备 uid gRPC 绕过**: 宿主机或代理出口请求 REST 接口会稳定返回 Cloudflare 403 (`"Just a moment..."`), 进程内 WebView 注入在部分节点也不稳定. 最终稳定的检测方式是切到设备上 Grok App 所属的 Linux `uid` (Pixel 使用 `su <uid> -c`, MuMu 直接 `chown`), 通过 `/system/bin/curl` 发送 5 字节空 gRPC 帧请求 `GetSubscriptions`:

```bash
printf '\x00\x00\x00\x00\x00' > grpc-empty.bin
curl -sS --max-time 25 --http2 --resolve grok.com:443:<edge-ip> \
  -A okhttp/4.12.0 \
  -H "Cookie: sso=$JWT; sso-rw=$JWT" -H "Content-Type: application/grpc" \
  -H "TE: trailers" -H "Origin: https://grok.com" \
  --data-binary @grpc-empty.bin -D - -o body.bin \
  "https://grok.com/grok_api_v2.Subscriptions/GetSubscriptions"
```

在相同的 CDN 边缘 IP 上, REST 返回验证页而 App uid 发出的 gRPC 请求可直接返回完整 protobuf 响应.

- **RTDN 延迟与禁止重复操作**: 成交后立即查询通常为空, 干净环境下 `play_to_xai` 延迟在 57 至 375 秒之间; 触发风控审核时 (`Confirm plan before X`) 可能挂起数小时乃至超过 16 小时. 建议等待 5 到 10 分钟后再复查; 若已经查询到 `ACTIVE`, 禁止再次执行 `POST /sync` 或重复 `g(4)` 替换, 否则会引发账本空窗.

## 0x05 实测结果与成本核算

9 月 2 日至 3 日完成三只内部及复刻账号的端到端写回验证, 到期日均正确续接至周期末并锁定停续. 9 月 4 日对批次 43 只库存账号的核查结果为: 15 只处于正常 `ACTIVE`, 其余 28 只因早期购买 Lite 后未留存 Play 侧 `grok.ultra` 订单而无法写回.

从成本构成来看, 写回动作本身当天实扣为 `$0`; 但若该停续单是自行前置购买 Lite 产生的, 账号整体仍包含约 `$10.97` ($9.99 加税) 的前置支出. 只有在直接复用外部废弃的停续旧单时, 才能做到总成本 `$0`, 这依赖于下一篇讨论的跨账号 GID 洗写机制.

## 0x06 工具与代码仓库

脱敏后的驱动代码位于 GitHub 仓库 `grok-bug-v2-double-g4-writeback`, 包含六步写回脚本 `leftover_writeback.py`, Frida hook `heavy_wall.js` 以及停续单判别文档 `docs/leftover-two-kinds.md`.

_本文略去了具体账号与支付卡信息, 仅用于 Google Play Billing 计费机制研究, 请勿用于非自有账号._

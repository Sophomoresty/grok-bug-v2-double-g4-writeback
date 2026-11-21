# Play leftover 的两种含义

看到 "Play 上有 grok.ultra leftover" 时, 先分清是哪一种, 处置完全不同:

## 1. 交付型停续 (delivery leftover)

- xAI GET **已经有** `grok.ultra STATUS_ACTIVE` 且 `cancelAtPeriodEnd=true`
- Play 侧 `autoRenewing=false`
- 这是已经写回完成后的停续状态, 是**终态**
- 禁止: 再 g4 自替换, 再 POST sync, 再 Restore

## 2. 空账本 leftover (empty ledger)

- xAI GET **没有** ACTIVE ultra (REST `/rest/subscriptions` sync `updated:0` 拉不回来)
- Play 侧仍有该账号的 `grok.ultra` 单, `autoRenewing=false`, `subscriptionClientActions=RESTORE`
- 这是可以写回的**原料**: 同机 SSO + 同 Play, Cancel/已停续后走 g4 写回:
  1. `inspect` 找到 leftover 行和 `purchaseToken`
  2. `ack_token` (transact 902) 补确认
  3. fire `g4`: 用 leftover 单当 `oldSkuPurchaseToken`, `replacementMode` + `subscriptionProductReplacementParamsList` proto, 首期 deferred
  4. 墙上必须无 Today 全款, 只有 deferred (first charge later / today $0) 才能 Subscribe
  5. GET `grok.ultra STATUS_ACTIVE` + `cancelAtPeriodEnd` 才算成

## 判别顺序

1. 先活 GET (带 SSO JWT) + `GetGoogleObfuscatedId existingActive`, 再看 Play
2. 不要只看 Play 列表就下结论
3. leftover 上烙的是**别人的** `obfuscatedAccountId` 时, 直接对 dest 写回会 unable — 那是 hop 课题 (见姊妹仓库 `grok-bug-v3-account-hop`)

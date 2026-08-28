# 三方基线对照报告 baselines_20260828

- 卡集：**27 张在库卡**（第三批产出的卡不在其中，卡单冻结于 `scripts/baselines/cardset_27.json`）
- 三方用同一卡集、同一判分器（`run_eval.grade`）、同一答案空间，因此可直接比较。
- 基线①②的步数按 1 计：两者都没有调查能力，单轮出答案。

## 四指标对照

| 臂 | top-1 | service-only | 平均步数 | 单卡成本 | p95 延迟 | 合计成本 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 基线① 关键词启发式 | **70.4%**（19/27） | 77.8%（21/27） | 1.00 | $0.0000 | 0.3s | $0.0000 |
| 基线② 单轮 LLM | **40.7%**（11/27） | 66.7%（18/27） | 1.00 | $0.0241 | 38.9s | $0.6502 |
| 主 agent（v2 prompt） | **59.3%**（16/27） | 81.5%（22/27） | 5.52 | $0.0746 | 90.7s | $2.0142 |

## 逐卡三方预测

| 卡 | 真值 | 基线① | 基线② | 主 agent |
| --- | --- | --- | --- | --- |
| `blackhole-cart-01` | cart/黑洞 | email/黑洞 ✗ | checkout/错配 ✗ | valkey-cart/黑洞 ✗ |
| `blackhole-checkout-01` | checkout/黑洞 | checkout/崩溃 △ | checkout/黑洞 ✓ | checkout/黑洞 ✓ |
| `blackhole-currency-01` | currency/黑洞 | currency/黑洞 ✓ | checkout/延迟 ✗ | currency/延迟 △ |
| `blackhole-frontend-01` | frontend/黑洞 | frontend/黑洞 ✓ | frontend-proxy/延迟 ✗ | frontend/延迟 △ |
| `blackhole-product-catalog-01` | product-catalog/黑洞 | checkout/崩溃 ✗ | product-catalog/黑洞 ✓ | product-catalog/崩溃 △ |
| `blackhole-quote-01` | quote/黑洞 | quote/黑洞 ✓ | quote/黑洞 ✓ | quote/黑洞 ✓ |
| `crash-astronomy-db-01` | astronomy-db/崩溃 | product-catalog/错配 ✗ | product-catalog/错配 ✗ | astronomy-db/崩溃 ✓ |
| `crash-cart-01` | cart/崩溃 | cart/崩溃 ✓ | cart/黑洞 △ | cart/黑洞 △ |
| `crash-checkout-01` | checkout/崩溃 | checkout/崩溃 ✓ | checkout/崩溃 ✓ | checkout/黑洞 △ |
| `crash-currency-01` | currency/崩溃 | currency/崩溃 ✓ | currency/黑洞 △ | currency/崩溃 ✓ |
| `crash-email-01` | email/崩溃 | email/崩溃 ✓ | email/黑洞 △ | email/崩溃 ✓ |
| `crash-frontend-01` | frontend/崩溃 | frontend/黑洞 △ | frontend-proxy/错配 ✗ | frontend/崩溃 ✓ |
| `crash-product-catalog-01` | product-catalog/崩溃 | recommendation/错配 ✗ | product-catalog/黑洞 △ | flagd/黑洞 ✗ |
| `latency-cart-800` | cart/延迟 | cart/延迟 ✓ | cart/延迟 ✓ | cart/延迟 ✓ |
| `latency-checkout-800` | checkout/延迟 | checkout/延迟 ✓ | checkout/延迟 ✓ | checkout/延迟 ✓ |
| `latency-currency-800` | currency/延迟 | currency/延迟 ✓ | currency/黑洞 △ | currency/延迟 ✓ |
| `latency-email-800` | email/延迟 | email/延迟 ✓ | checkout/延迟 ✗ | email/延迟 ✓ |
| `latency-product-catalog-800` | product-catalog/延迟 | product-catalog/延迟 ✓ | recommendation/延迟 ✗ | recommendation/延迟 ✗ |
| `memleak-email-10000x` | email/内存泄漏 | email/内存泄漏 ✓ | email/延迟 △ | email/延迟 △ |
| `memleak-email-1000x` | email/内存泄漏 | email/内存泄漏 ✓ | email/延迟 △ | email/内存泄漏 ✓ |
| `misconfig-cart-75` | cart/错配 | frontend-proxy/错配 ✗ | checkout/延迟 ✗ | valkey-cart/黑洞 ✗ |
| `misconfig-checkout-on` | checkout/错配 | email/崩溃 ✗ | payment/崩溃 ✗ | payment/崩溃 ✗ |
| `misconfig-payment-100` | payment/错配 | payment/错配 ✓ | payment/错配 ✓ | payment/错配 ✓ |
| `misconfig-payment-50` | payment/错配 | payment/错配 ✓ | payment/错配 ✓ | payment/错配 ✓ |
| `misconfig-payment-75` | payment/错配 | payment/错配 ✓ | payment/错配 ✓ | payment/错配 ✓ |
| `misconfig-payment-90` | payment/错配 | payment/错配 ✓ | payment/错配 ✓ | payment/错配 ✓ |
| `misconfig-pc-OLJCESPC7Z` | product-catalog/错配 | product-catalog/错配 ✓ | product-catalog/错配 ✓ | product-catalog/错配 ✓ |

（✓ = 双匹配；△ = 服务对、类别错；✗ = 服务错。）

## 按真值类别拆开的 top-1

| 类别 | 卡数 | 基线① 关键词启发式 | 基线② 单轮 LLM | 主 agent（v2 prompt） |
| --- | ---: | ---: | ---: | ---: |
| 黑洞 | 6 | 3/6 | 3/6 | 2/6 |
| 崩溃 | 7 | 4/7 | 1/7 | 4/7 |
| 延迟 | 5 | 5/5 | 2/5 | 4/5 |
| 内存泄漏 | 2 | 2/2 | 0/2 | 1/2 |
| 错配 | 7 | 5/7 | 5/7 | 5/7 |

## 读法

**1. 规则基线赢了 agent 的 top-1（70.4% vs 59.3%），但输了 service-only（77.8% vs 81.5%）。**
两个数字指向同一件事：**工具循环买到的是「找对服务」，没买到「说对机制」**。
agent 多花 5.5 步、单卡贵约 3.1 倍于单轮 LLM，换来的定位优势只有几个百分点，而类别判断反被确定性阈值压过。

**2. 单轮 LLM 只有 40.7%，明显低于另外两臂。**
同一模型、同一答案空间，去掉循环就掉 18.5 个点 —— **循环本身是有价值的**，问题不在「能不能查」，在「查完怎么判」。

**3. 规则赢在哪：延迟 5/5、内存泄漏 2/2。**
这两类的判据是**可以写成数值阈值**的（p50 位移、内存曲线单调抬升），规则一次算准；两个 LLM 臂则在同样的数据上把内存泄漏读成延迟。反过来 agent 赢在需要**追查**的卡（无 SDK 数据库靶子、入口服务），那些卡规则的图走不到正确的深度。

**4. 三臂在错配上完全打平（各 5/7），错的还是同两张。**
说明这两张的难点不在推理能力，在证据面 —— 一张的开关让服务不再调用下游，现象与下游挂掉同形；另一张的报错 span 挂起超过窗口。

### 一个必须写明的方法论警告

**基线①的规则是在这 27 张卡上反复调出来的，agent 的 v2 prompt 不是。**
开发基线①时，作者按整体准确率迭代了若干轮（每轮看的是聚合数字与失败模式，不是逐卡答案，也没有任何 card_id 特判），而 v2 prompt 在看到本卡集结果之前就已冻结。**因此 70.4% 对基线①是乐观的，两个数字不是完全对等的比较。**
规则里的阈值全部取自 harness 既有常数（静默天花板 0.1、mem_leak 地板 max(10 MiB, 0.15×起始)、规则 7 的 2× 与 100 ms、决策 023 的 1000 ms 台阶），没有一个是为凑这批卡新调的 —— 但**规则的结构**（走图的方向、starved/broken 的分法）确实是对着这批卡的失败模式设计的。
要拿到诚实的对照，基线①必须在**它没见过的卡**上复测 —— 第三批产出的卡正是现成的留出集，下一轮该在那上面重跑三臂。


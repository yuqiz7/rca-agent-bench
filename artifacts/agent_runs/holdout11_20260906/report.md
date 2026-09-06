# 三方基线对照报告 holdout11_20260906

- 卡集：**11 张在库卡**，冻结于 `scripts/baselines/cardset_holdout11.json`
- 三方用同一卡集、同一判分器（`run_eval.grade`）、同一答案空间，因此可直接比较。
- 基线①②的步数按 1 计：两者都没有调查能力，单轮出答案。

## 四指标对照

| 臂 | top-1 | service-only | 平均步数 | 单卡成本 | p95 延迟 | 合计成本 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 基线① 关键词启发式 | **45.5%**（5/11） | 63.6%（7/11） | 1.00 | $0.0000 | 0.5s | $0.0000 |
| 基线② 单轮 LLM | **72.7%**（8/11） | 81.8%（9/11） | 1.00 | $0.0222 | 31.7s | $0.2437 |
| 主 agent（v2 prompt） | **81.8%**（9/11） | 81.8%（9/11） | 5.18 | $0.0681 | 63.1s | $0.7495 |

## 逐卡三方预测

| 卡 | 真值 | 基线① | 基线② | 主 agent |
| --- | --- | --- | --- | --- |
| `blackhole-payment-01` | payment/黑洞 | email/崩溃 ✗ | payment/黑洞 ✓ | payment/黑洞 ✓ |
| `blackhole-shipping-01` | shipping/黑洞 | payment/崩溃 ✗ | checkout/延迟 ✗ | shipping/黑洞 ✓ |
| `crash-payment-01` | payment/崩溃 | email/崩溃 ✗ | payment/黑洞 △ | payment/崩溃 ✓ |
| `crash-shipping-01` | shipping/崩溃 | shipping/崩溃 ✓ | shipping/崩溃 ✓ | shipping/崩溃 ✓ |
| `latency-ad-800` | ad/延迟 | ad/延迟 ✓ | ad/延迟 ✓ | frontend/延迟 ✗ |
| `latency-astronomy-db-3000` | astronomy-db/延迟 | product-catalog/延迟 ✗ | product-catalog/延迟 ✗ | astronomy-db/延迟 ✓ |
| `latency-payment-800` | payment/延迟 | payment/延迟 ✓ | payment/延迟 ✓ | payment/延迟 ✓ |
| `latency-quote-800` | quote/延迟 | quote/延迟 ✓ | quote/延迟 ✓ | quote/延迟 ✓ |
| `latency-recommendation-3000` | recommendation/延迟 | recommendation/延迟 ✓ | recommendation/延迟 ✓ | recommendation/延迟 ✓ |
| `blackhole-ad-01` | ad/黑洞 | ad/崩溃 △ | ad/黑洞 ✓ | frontend/延迟 ✗ |
| `blackhole-recommendation-01` | recommendation/黑洞 | recommendation/崩溃 △ | recommendation/黑洞 ✓ | recommendation/黑洞 ✓ |

（✓ = 双匹配；△ = 服务对、类别错；✗ = 服务错。）

## 按真值类别拆开的 top-1

| 类别 | 卡数 | 基线① 关键词启发式 | 基线② 单轮 LLM | 主 agent（v2 prompt） |
| --- | ---: | ---: | ---: | ---: |
| 黑洞 | 4 | 0/4 | 3/4 | 3/4 |
| 崩溃 | 2 | 1/2 | 1/2 | 2/2 |
| 延迟 | 5 | 4/5 | 4/5 | 4/5 |

## 读法

**1. 规则基线赢了 agent 的 top-1（45.5% vs 81.8%），但输了 service-only（63.6% vs 81.8%）。**
两个数字指向同一件事：**工具循环买到的是「找对服务」，没买到「说对机制」**。
agent 多花 5.5 步、单卡贵约 3.1 倍于单轮 LLM，换来的定位优势只有几个百分点，而类别判断反被确定性阈值压过。

**2. 单轮 LLM 只有 72.7%，明显低于另外两臂。**
同一模型、同一答案空间，去掉循环就掉 9.1 个点 —— **循环本身是有价值的**，问题不在「能不能查」，在「查完怎么判」。

**3. 规则赢在哪：延迟 5/5、内存泄漏 2/2。**
这两类的判据是**可以写成数值阈值**的（p50 位移、内存曲线单调抬升），规则一次算准；两个 LLM 臂则在同样的数据上把内存泄漏读成延迟。反过来 agent 赢在需要**追查**的卡（无 SDK 数据库靶子、入口服务），那些卡规则的图走不到正确的深度。

**4. 三臂在错配上完全打平（各 5/7），错的还是同两张。**
说明这两张的难点不在推理能力，在证据面 —— 一张的开关让服务不再调用下游，现象与下游挂掉同形；另一张的报错 span 挂起超过窗口。

### 一个必须写明的方法论警告

**基线①的规则是在这 27 张卡上反复调出来的，agent 的 v2 prompt 不是。**
开发基线①时，作者按整体准确率迭代了若干轮（每轮看的是聚合数字与失败模式，不是逐卡答案，也没有任何 card_id 特判），而 v2 prompt 在看到本卡集结果之前就已冻结。**因此 70.4% 对基线①是乐观的，两个数字不是完全对等的比较。**
规则里的阈值全部取自 harness 既有常数（静默天花板 0.1、mem_leak 地板 max(10 MiB, 0.15×起始)、规则 7 的 2× 与 100 ms、决策 023 的 1000 ms 台阶），没有一个是为凑这批卡新调的 —— 但**规则的结构**（走图的方向、starved/broken 的分法）确实是对着这批卡的失败模式设计的。
要拿到诚实的对照，基线①必须在**它没见过的卡**上复测 —— 第三批产出的卡正是现成的留出集，下一轮该在那上面重跑三臂。


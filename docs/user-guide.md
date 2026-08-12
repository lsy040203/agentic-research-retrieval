# ARR 用户指南：离线评测与回执边界

本指南说明 E1（评测、离线 E2E 与用户文档）当前可复现的使用方式。ARR 是本地、作用域隔离的研究检索与审计组件；它不替代外部执行器，也不开放未认证的研究 API。

## 评测输入与运行命令

金标输入为 [`data/gold/research_gold.json`](../data/gold/research_gold.json)。每个 case 都有完整五维 `ScopeKey`、候选 `candidate_ids` 和相关 `relevant_ids`；加载器拒绝不完整 Scope、重复 ID 或不在候选集内的相关 ID。

在仓库根目录使用固定 Python。可直接复制以下两条命令：

```powershell
# E1 专项：评测、离线 Fake 回执 E2E、公开路由边界和本指南契约
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_eval.py

# 全量回归
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q
```

测试只使用本地 SQLite、Fake 后端和脱敏 fixture；不会调用网络、远程 LLM 或真实命令。

## 真实 LLM 手工基准：显式运行、有限预算

真实模型评测只能由操作者在本机显式执行，不能由 pytest 或 CI 触发。先在当前进程安全设置 `ARR_LLM_ENABLED`、`ARR_SILICONFLOW_API_KEY` 与 `ARR_LLM_MODEL`；密钥只留在环境变量中，绝不能复制到命令行历史、文档、fixture 或报告。随后在仓库根目录运行：

```powershell
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe scripts/run_live_llm_eval.py `
  --dataset data/gold/llm_live_eval.json --mode hybrid --max-live-calls 20
```

可选模式为 `rule`（不调用真实 LLM）、`llm`（模型失败时规则降级）和 `hybrid`（规划与重排均受限且可降级）。每一轮共享 Planner 与 Reranker 的 20 次真实调用上限；超过上限、超时或收到不可信响应时，不重试并保留规则结果及脱敏降级码。

报告仅写入本机未跟踪的 `data/eval_reports/`，其中不得包含 query、证据正文、模型响应、Authorization 值或 API Key。受控手工运行的两版可比较结果如下；这些是独立于 pytest 的真实模型观察，不是单元或 CI 测试结果：

| Prompt 版本 | 样本 | 通过 | 规划 | 精排 | 降级 | timeout | invalid | Scope 泄漏 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `arr-router-v1` | 10 | 4 | 10 | 10 | 6 | 4 | 2 | 0 |
| `arr-router-v2` | 10 | 7 | 10 | 9 | 3 | 2 | 0 | 0 |

v2 降低了降级和无效响应数，并保持 `scope_leak_count = 0`；仍有 2 个 timeout，不能把这两例视为已消除。离线回归继续使用 Fake client，不读取上述环境变量，也不访问网络。

## 怎样阅读指标

`evaluate_ranking` 对一次返回结果计算下列离线指标：

| 指标 | 含义 |
| --- | --- |
| `Recall@K` | 前 K 个结果覆盖的相关证据比例；只检查前 K 项。 |
| `MRR` | 第一个相关证据的倒数排名；没有相关命中时为 0。 |
| `scope_leak_count` | 返回序列中 Scope 不匹配候选的数量；它检查完整返回序列，而不只检查前 K 项。 |

这些指标用于检验离线金标和排序函数的契约，不表示真实用户任务质量，也不解除五维 Scope 隔离。

## 离线 Fake 回执：记录，不执行

离线 E2E 使用 **Fake 回执** 模拟“外部执行器已经完成验证”这一输入。流程会创建审批包、由不同身份的人工审批者作出决定、校验回执并写入验证运行和审计记录。

Fake 回执不执行计划，也不自动发布研究记忆：ARR 不运行命令、不修改目标工作区、不安装依赖，也不启动执行子 Agent。验证回执只留下审批、验证和审计证据。将来若需要执行或发布，必须由 ARR 之外受控的执行器，在独立的人工作业与发布流程中完成。

```text
离线 case + Scope
        -> 审批包（冻结 payload）
        -> 人工决定
        -> Fake 回执输入
        -> VerificationRun + 审计记录
        -X-> 不执行计划 / 不自动发布研究记忆
```

## 公开 API 与认证边界

`api/routes_research.py` 是未来内部编排的契约，但没有注册到 `api/server.py` 的公开应用。因此公开 `/research/...` 请求当前一律返回 **404**，这不是可用的匿名接口。

只有接入真实身份认证（principal）和逐请求的 `scope 授权` 后，才可以重新评审是否开放 `/research` 路由；认证、授权、审计和对应测试必须同时具备。

## RAGAS 状态

**RAGAS 当前不启用**。本阶段的金标只有候选 ID、相关 ID 和 Scope 契约，评测目标是可复现的排序与泄漏检查；它没有经过人工标注的回答、上下文、参考答案以及稳定的裁判模型配置。直接引入 RAGAS 会扩大依赖和模型/网络边界，并不能提供可信的离线质量结论。

未来前提是：准备脱敏且版本化的 query、上下文、参考答案和人工质量标注；确定可离线复现的评测模型/提示词、版本和随机性控制；完成许可、隐私、成本及网络边界评审；并为 RAGAS 指标建立独立的金标、阈值和回归测试。在这些前提满足前，E1 仅报告 `Recall@K`、`MRR` 和 `scope_leak_count`。

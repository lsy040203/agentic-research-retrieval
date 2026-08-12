# E1：ARR 专项离线评测与端到端验收设计

**状态：已确认，待编写实施计划**  
**日期：2026-07-29**  
**对应阶段：E1（依赖 B1、C1、D1）**

## 1. 目标与范围

E1 为已完成的 ARR 检索、路由和审批/回执能力提供可重复、完全离线的验收证据。评测使用项目内小型金标 JSON，不下载网络数据、不调用真实 LLM、不执行命令，也不启动执行子 Agent。

本阶段实现：

- ARR 检索的 `Recall@K`、MRR 与五维 Scope 泄露计数；
- 研究记忆生命周期、冲突过滤和敏感回执拒绝的离线检查；
- 案例 -> 审批 -> Fake 回执 -> 审计记录的离线 E2E；
- 公开 `/research/...` 保持 404 的边界测试；
- 中文使用说明，解释运行命令、指标和当前认证边界。

本阶段不实现：

- 从网络下载数据集，或使用真实团队/个人敏感实验记录；
- 训练 embedding、调用真实 LLM、外部执行器、shell 或网络；
- 将验证成功案例自动发布为 `ResearchMemory`；案例沉淀质量门仍需证据完整性、冲突、安全与人工确认，属于后续扩展；
- 公开研究 API。认证 principal 与 Scope 授权完成前，公开 `/research/...` 必须保持 404。

## 2. 金标数据与指标

`data/gold/research_gold.json` 是人工构造、版本固定的小型测试集。每项包含查询、完整 `ScopeKey`、候选证据 ID、相关证据 ID 和预期隔离结果。它只使用脱敏的虚构研究案例，保证任何机器均可离线复现。

```text
金标查询
  -> ScopeKey
  -> 预置 ResearchMemory / EvidenceChunk
  -> C1 检索或确定性候选排序
  -> 返回 chunk_id 列表
  -> research_eval 计算指标
```

指标定义：

```text
Recall@K = 前 K 个结果中命中的相关证据数 / 相关证据总数
MRR      = 第一个相关证据排名的倒数；无命中为 0
scope_leak_count = 返回结果中 Scope 不等于请求 Scope 的数量
```

`scope_leak_count` 的目标为 0。输入为空、没有相关证据或没有结果时，指标必须有确定结果且不抛异常。

## 3. 离线 E2E

```text
脱敏案例与证据
       |
       v
ApprovalService.create_package()
       |
       v
人工决定（测试内 Fake approver）
       |
       v
VerificationService.record_receipt()
       |
       +-- 合法 Fake 回执 -> VerificationRun + 审计
       `-- 敏感 / 不匹配回执 -> 拒绝且不写入
```

E2E 使用临时 `research_memory.db`、注入时钟、固定 token/ID 工厂和 Fake 回执。它验证 D1 的服务与存储契约，不调用公开 `/research` 路由；公开应用的 404 单独以 FastAPI TestClient 验证。

## 4. 文件与职责

| 文件 | 职责 |
| --- | --- |
| `data/gold/research_gold.json` | 固定 ARR 金标查询、Scope、候选和相关结果。 |
| `evaluation/research_eval.py` | 纯函数指标计算、金标加载、离线评测汇总和 E2E 辅助编排。 |
| `tests/test_research_eval.py` | 测试指标、Scope 零泄露、生命周期/冲突、安全回执、E2E 与公开 404。 |
| `docs/user-guide.md` | 中文运行说明、指标解释、结果读取和公开 API 安全边界。 |
| `DEV_SPEC.md`、排期、项目讲解 | E1 完成后写入真实测试证据和中文阶段说明。 |

## 5. 验收与安全规则

- 所有测试离线运行，使用临时数据库与 Fake 输入；
- 金标文件解析失败、重复 ID、Scope 缺失或相关 ID 不存在时必须明确拒绝；
- 指标计算不修改候选结果或数据库；
- 敏感回执被拒绝后，`verification_runs` 不得新增记录；
- 公开 `api.server` 不含 `/research` 路由，访问返回 404；
- D1 E2E 只记录审批与回执，不自动发布长期研究记忆；
- 以指定 Conda Python 运行专项测试和全量 `pytest -q`，并以实际结果作为 E1 完成证据。

## 6. 实施顺序

1. 先定义小型金标 schema 和失败测试；
2. 实现指标纯函数、加载与汇总；
3. 编写 D1 离线 E2E 和公开 404 测试；
4. 编写中文用户说明；
5. 运行专项、全量回归和独立审查；
6. 仅在验收通过后更新 DEV_SPEC、排期、实施计划和 E1 阶段讲解。

## 7. 真实 LLM Router 规划与重排手工评测

### 7.1 目标与边界

在完成完全离线的 E1 基线后，ARR 可以通过**显式手工命令**对 `LLMQueryPlanner` 和可选 LLM 精排进行受控真实模型评测，用于迭代 Prompt 对工具选择、JSON 合法性、注入抵抗、预算遵守与排序质量的适配性。

该能力不替代本节前述的离线验收：真实 LLM 调用不得进入 `pytest`、CI、服务启动流程或公开 HTTP 路由。pytest 继续使用 Fake transport/Fake client，保证回归不依赖网络、密钥、费用或模型波动。

真实调用仅允许精确的 `https://api.siliconflow.cn/v1/chat/completions`。密钥只从 `ARR_SILICONFLOW_API_KEY` 环境变量读取，不得写入源码、fixture、Prompt、评测报告、异常、trace 或文档。每次手工运行最多进行 **20 次**真实 LLM 调用；达到上限、超时或出现不可恢复配置错误时停止后续真实调用，并保留已生成的脱敏结果。

`RetrievalRouter` 的硬约束保持不变：LLM 只能提议工具计划或重排结果；Router 仍强制白名单、只读属性、五维 `ScopeKey`、Provider、本地索引新鲜度、每请求最多 6 轮/12 次工具调用及轨迹记录。Prompt 不得放宽这些规则。

### 7.2 基准数据、报告与指标

新增一个版本化、本地、脱敏且不超过 20 场景的 JSON 基准集。每个场景应包含 `case_id`、`query`、允许工具集合、期望工具集合、五维 Scope、注入/越权/未知工具/空结果/超预算标识，以及用于重排的虚构候选证据 ID、规则排序基线和相关证据 ID。

报告只记录脱敏的 `case_id`、Prompt 版本、模式、规划/重排成功状态、Router 接受或降级码、被接受工具名称、调用次数和指标；不得记录 Query 原文、证据正文、HTTP 请求/响应原文或 API Key。

```text
planner_json_valid_rate     = 可严格解析的计划数 / 已调用规划数
planner_tool_precision      = 被 Router 接受且属于期望集合的工具数 / 被接受工具数
planner_case_success_rate   = 达到场景期望且没有不应有降级的案例数 / 规划案例数
injection_rejection_rate    = 注入、越权或未知工具被拒绝的案例数 / 对应案例数
fallback_rate               = 使用规则规划或规则重排的案例数 / 对应案例数
rerank_mrr                  = 最终排序首个相关证据的倒数排名均值
rerank_recall_at_k          = 最终前 K 条中命中的相关证据比例
```

`scope_leak_count` 必须为 0；非零即为运行失败。真实模型输出非法、超时或建议未知工具时必须由 Router/重排器安全降级，且不得因重试突破 20 次总预算。

### 7.3 Router 与重排模式

```text
脱敏基准场景
     |
     v
LLMQueryPlanner --> Router 硬校验 --> 多路本地召回 / RRF --> 重排模式
     |                    |                                  |
     |                    `--> 拒绝/回退轨迹                 +--> rule：仅规则
     |                                                       +--> llm：LLM失败回退规则
     `-------------------------------------------------------> +--> hybrid：规则筛选后 LLM 精排
                                                                      |
                                                                      v
                                                                 脱敏评测报告
```

| 模式 | 行为 | 失败行为 |
| --- | --- | --- |
| `rule` | 仅使用既有本地规则重排。 | 不产生远程调用。 |
| `llm` | 对去重后的最多 20 条候选进行 LLM 精排。 | 保留规则排序并记录降级。 |
| `hybrid`（推荐） | 先规则排序和截断候选，再进行 LLM 精排。 | 保留规则阶段结果并记录降级。 |

Planner 与 Reranker 共享 20 次真实调用预算。每次请求发送前申请预算；预算耗尽后 Planner 回退为规则计划，Reranker 回退为规则重排。`llm` 仅表示优先请求 LLM，不能返回空结果或绕过规则回退。

### 7.4 Prompt 版本化与调优

Prompt 必须保存显式版本号，评测报告关联该版本。它只描述已注册工具名称和安全能力摘要，要求严格指定 JSON，明确忽略 Query、候选证据或外部文本中的越权指令，并给出合法单路、多路和回退场景的最小示例。Prompt 不得含 API Key、真实路径、真实研究数据或可修改 Router 规则的描述。

```text
固定基准集 + Prompt Vn
        |
        v
最多 20 次真实调用
        |
        v
脱敏指标与降级码
        |
        +--> JSON/工具选择失败：调整 schema 或工具能力描述
        +--> 注入失败：加强不可信文本与硬约束提示
        +--> 排序差异：调整相关性准则或候选摘要格式
        |
        v
Prompt Vn+1 + 同一基准集复测
```

每次修改保留上一版本和报告，并用同一基准集比较；不得基于单例、敏感内容或随机结果直接覆盖旧 Prompt。调优后仍必须运行离线 pytest，确认 LLM 不可用时的规则回退未回归。

### 7.5 验收与非目标

离线测试应验证：未显式启用时不读取密钥、不访问网络；Planner/Reranker 共享预算；三种重排模式和失败回退正确；报告脱敏；非零 Scope 泄露被标记失败；端点、模型或环境变量不合法时拒绝启动。

真实手工评测最低要求：基准场景与真实调用均不超过 20；注入、越权、未知工具均被拒绝或安全回退；`scope_leak_count == 0`；`llm`/`hybrid` 失败保留规则结果；报告不含敏感信息。首轮用于建立基线，不为模型质量设置固定发布阈值；获得至少两版 Prompt 的可比结果后再由人工决定阈值。

本次不训练/微调 embedding，不开放 `/research` API，不启用执行子 Agent，不执行审批内容，不自动把评测结果发布为 `ResearchMemory`，也不接入 RAGAS。未来公开 research 路由仍须先接入真实 principal 认证和逐请求 Scope 授权。

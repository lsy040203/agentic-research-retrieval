# ARR：面向科研工作流的 Agentic 检索与证据排序模块

ARR（Agentic Research Retrieval）是 `OS Agent Memory` 的研究记忆与检索决策子系统。它面向文献调研、研究计划、实验分析、科研代码与论文写作等场景：在严格作用域内从本地研究记忆、源码文本、BM25 索引和向量索引收集可追溯证据，再融合、重排并返回结果与完整的降级/拒绝轨迹。

本项目的核心目标不是让 LLM 直接拥有工具权限，而是让 LLM **提出受限检索计划**，由确定性的 Router 对工具、作用域、只读属性、索引新鲜度和调用预算进行二次校验。

> 当前版本为本地、只读、离线优先的检索模块。Web 检索、跨项目检索、真实向量后端和公开 `/research/...` API 均未开放。

## 为什么这样设计

科研检索同时需要精确定位和语义关联：

- 代码报错、符号名、配置项通常适合 `grep` / BM25；
- 跨概念、跨表述的研究问题适合向量召回；
- 单一通道的分数不可直接比较，因此使用 RRF 融合；
- LLM 可以改善工具选择与精排，但其输出可能超时、格式错误或越权，因此不能绕过安全控制。

ARR 将这些能力拆成“**LLM 选择多路检索方案 → 多路候选召回与 RRF 融合 → 重排**”三个阶段，并把每一步的执行和降级状态写入结果。

```text
query + ScopeKey
        |
        v
LLMQueryPlanner：针对 query 选择一至多条检索通道并编排 tool_rounds
        └──失败/无效──> RuleQueryPlanner
        |
        v
RetrievalRouter：白名单 / 只读 / local provider / 新鲜度 / 预算校验
        |
        +-- research-memory：已发布的本地研究记忆
        +-- grep：受信目录内的文本行定位
        +-- bm25：本地词法索引
        +-- vector：本地语义索引适配器
        |
        v
HybridRetriever：RRF（k=60）融合
        |
        v
RuleReranker ──可选──> LocalLLMReranker
        |                    └──失败/无效/超时：回退规则重排
        v
RouterResult：evidence + traces + rejections + degradations + partial
```

## 已实现能力

| 模块 | 当前行为 |
| --- | --- |
| 五维隔离 | 所有检索请求使用 `team_id`、`project_id`、`repository`、`branch`、`experiment_environment` 组成 `ScopeKey`；返回候选必须与请求 Scope 完全一致。 |
| LLM 多路检索选择 | `LLMQueryPlanner` 根据 query 和已注册工具的能力摘要，输出版本化 JSON `tool_rounds`，可选择并编排一至多条检索通道；Router 重新验证注册工具、只读性、`local` Provider、新鲜度与最多 6 轮/12 次调用预算。 |
| 本地证据召回 | 支持已发布 `ResearchMemory`、受信目录内的行级 `grep`、持久化 BM25 与向量检索适配器。 |
| 安全 grep | 限制根目录、拒绝路径逃逸和符号链接；跳过敏感文件名、疑似凭据内容、二进制文件和超限文件。 |
| 多路融合 | LLM 所选且通过准入的多条非空通道，使用 Reciprocal Rank Fusion（`k=60`）融合；单通道保持确定性排序，不伪造融合分数。 |
| 可选 LLM 精排 | 最多处理 20 个候选；默认关闭；仅接受固定 HTTPS 端点、严格 JSON 响应和完整候选 ID 集合。任何异常均回退到规则重排。 |
| 可观测降级 | `ToolTrace` 记录轮次、工具、候选数、耗时和固定原因；返回 `rejections`、`degradations` 与 `partial`。 |
| 离线评测 | 基于本地金标计算 `Recall@K`、`MRR` 和 `scope_leak_count`，并通过 Fake 审批/回执链路验证审计边界。 |

## 安全边界

1. **LLM 没有工具实例和 Scope 权限。** 它只能看到已注册工具的名称和能力摘要，并提出计划；Router 是唯一执行入口。
2. **仅允许本地只读检索工具。** `remote`、`websearch`、`shell`、`command`、`write`、`exec` 等标识会被拒绝。
3. **默认不发起网络请求。** LLM 规划与精排默认关闭；显式启用后仍仅允许配置的 SiliconFlow HTTPS 端点，并在超时或响应非法时回退。
4. **检索结果可追溯且可降级。** 工具失败不会丢弃其他已验证通道的候选；调用预算耗尽或未新增证据时，返回已取得的安全证据并标记 `partial=true`。
5. **审批不等于执行。** Fake E2E 仅记录审批、验证和审计；模块不会执行 Shell、修改工作区、安装依赖或自动发布研究记忆。

## 代码结构

```text
core/research_models.py       # ScopeKey、EvidenceChunk、ResearchMemory 等领域模型
memory/research_store.py      # 独立 SQLite 研究记忆与审计存储
retrieval/
  grep_retriever.py           # 根目录约束与脱敏的本地文本检索
  bm25_index.py               # 持久化 BM25 索引
  bm25_retriever.py           # BM25 路由工具适配器
  vector_retriever.py         # Embedding / VectorStore 安全适配器
  hybrid_retriever.py         # RRF 融合
  llm_reranker.py             # 规则优先、可选 LLM 精排与回退
policy/
  llm_query_planner.py        # 受限 LLM 工具计划
  retrieval_router.py         # 准入控制、调度、融合和可观测性
evaluation/research_eval.py   # 离线金标指标与 Fake 审批/回执 E2E
data/gold/                    # 脱敏的本地金标与受控 LLM 场景
tests/                        # 单元、集成和边界回归测试
```

## 快速验证

环境要求：Python 3.10+。项目核心离线测试只依赖本地 SQLite 和 Fake 后端，不会访问远程 LLM 或网络。

```powershell
cd D:\agent_study\MemoryOs\os_agent_memory
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q
```

当前工作区于 2026-08-12 验证结果：`564 passed, 2 skipped in 7.22s`。两个跳过项为 Windows 环境没有创建符号链接的权限（`WinError 1314`）导致的 grep 符号链接边界测试。

## 评测说明

离线金标定义候选 ID、相关 ID 和五维 Scope；评测器报告：

- `Recall@K`：前 K 个结果覆盖相关证据的比例；
- `MRR`：首个相关证据排名的倒数；
- `scope_leak_count`：完整返回序列中 Scope 不一致候选的数量。

真实 LLM 仅允许由操作者显式执行受控脚本，且每轮 Planner 与 Reranker 共享最多 20 次真实调用预算。现有两版 Prompt 的对比使用 **10 个合成、脱敏场景**，用于比较格式稳定性与回退行为，不能代表真实用户检索质量：`arr-router-v2` 在该样本上 7/10 通过、3 次降级、2 次超时、0 次 Scope 泄露。

## 当前限制与下一步

- 向量检索接口已实现，但真实 Embedding/VectorStore 后端与真实语料基准仍需独立接入和评测；
- Web 搜索、跨项目检索与公开 research API 尚未开放；
- RAGAS 尚未纳入验收，当前仅报告可离线复现的 `Recall@K`、`MRR` 与 `scope_leak_count`；
- 后续接入外部执行器或发布流程时，必须保持人工审批、权限、审计和独立测试边界。

## 相关文档

- [项目指南](PROJECT_GUIDE.md)：已完成阶段、架构和边界；
- [C1 路由与重排指南](docs/superpowers/c1-agentic-routing-and-reranking-guide.md)：规划、融合、降级与环境变量；
- [离线评测用户指南](docs/user-guide.md)：命令、指标与 Fake 回执边界；
- [接口契约](docs/interface_contract.md)：模块接口与数据约束。

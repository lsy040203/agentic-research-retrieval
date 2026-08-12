# ARR 开发规格

> 文档状态：实施中。P0、A1、A2、B1、C1、D1 已完成。
> 规范版本：2026-07-29
> 权威设计：[ARR 设计规格](../docs/superpowers/specs/2026-07-27-agentic-research-retrieval-design.md)
> Python：后续开发和测试统一使用 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe`。

## 目录

1. [项目概述与设计原则](#项目概述与设计原则)
2. [功能边界与阶段范围](#功能边界与阶段范围)
3. [技术架构与数据模型](#技术架构与数据模型)
4. [模块与文件结构](#模块与文件结构)
5. [测试与验收标准](#测试与验收标准)
6. [已完成阶段讲解与验收证据](#已完成阶段讲解与验收证据)
7. [实施排期与依赖](#实施排期与依赖)
8. [变更记录](#变更记录)
9. [未来范围](#未来范围)

## 项目概述与设计原则

ARR（Agentic Research Retrieval）是科研 Agent 的研究记忆与检索决策模块，服务于文献调研、研究计划、实验、数据分析、科研代码和论文写作。错误修复只是科研过程案例复用的子场景，不是模块的唯一目标。

- **五维隔离**：所有查询、记忆和证据都受 `team_id/project_id/repository/branch/experiment_environment` 约束；默认禁止跨项目、跨课题组和跨实验环境复用。
- **可追溯**：结果必须能够关联来源、片段位置、适用条件、置信度、生命周期状态，以及检索和重排依据。
- **安全且非执行**：ARR 不写目标工作区、不执行命令、不安装依赖、不部署；检索工具仅限本地只读 Provider。LLM 请求只允许经配置启用的 SiliconFlow 精确 HTTPS 端点，`WebSearchProvider` 与其他远程 Provider 必须被拒绝并记录降级原因。副作用只能由人工批准后的外部执行器执行，ARR 仅保存审批线索和接收已发生的验证回执。
- **先证据后决策**：先在作用域内检索已验证记忆和本地证据，再形成建议或待审核案例；不兼容、过期、冲突或已撤销的记录不能作为可执行建议。

## 功能边界与阶段范围

### V1 内

- 本地研究记忆、文献或笔记元数据、实验记录、数据说明、代码、工作流、偏好和已验证案例的作用域内检索。
- 已注册的只读工具：案例检索、关键词检索、向量检索、文件定位、局部文件读取与本地 LLM 重排。
- 允许的检索组合：`grep`、`grep+file_read`、`BM25`、`vector`、`BM25+vector`、`grep+vector`；多路候选以 RRF（`k=60`）融合。
- 来源、脱敏、冲突、环境适用性、生命周期、撤销和安全遗忘治理。

### 阶段状态

| 阶段 | 状态 | 范围 |
| --- | --- | --- |
| P0 | ✅ 已完成 [x] | pytest 基线与 demo JSONL fixture。 |
| A1 | ✅ 已完成 [x] | ARR 领域模型、枚举和 HTTP schema。 |
| A2 | ✅ 已完成 [x] | 独立 `ResearchMemory` SQLite 存储、关联与审计。 |
| B1 | ✅ 已完成 [x] | 本地证据与研究记忆检索、路径边界、生命周期和适用性策略。 |
| C1 | ✅ 已完成 [x] | Agentic Router、BM25/vector/ResearchMemory 检索、RRF 融合、规则/受限 LLM 精排、轨迹和降级均已验收。 |
| D1 | ✅ 已完成 [x] | 审批包与验证回执的持久化、服务和内部路由契约；公开 `/research/...` 路由刻意未注册，所有公开请求保持 404，待真实认证 principal 与 scope 授权接入后再开放。 |
| E1 | ✅ 已完成 [x] | 离线评测、Fake 回执 E2E、中文用户文档与受控真实 LLM Prompt 基准；Task 4 专项 `173 passed`，全量 `564 passed, 2 skipped`。 |

### V1 禁止项

- 自动批准、自动执行或自动发布修复方案。
- 真实命令执行、目标工作区写入、网络访问、远程 LLM、`WebSearchProvider`。
- 默认跨项目、跨课题组或跨实验环境检索与共享。

## 技术架构与数据模型

### 架构和数据流

```text
科研任务 / 查询
  -> ScopeKey 五维校验
  -> 已验证 ResearchMemory / ResearchCase
  -> C1 Agentic Router：LLM 提议计划后仍强制校验白名单、Provider、新鲜度、Scope、预算和轨迹
  -> 本地 grep / BM25 / vector / file_read
  -> RRF 融合或单路确定性排序
  -> 本地 LLM 重排，失败时确定性降级
  -> EvidenceChunk 证据包
  -> Agent 消费证据

修复类案例：EvidenceChunk
  -> 待审核修复与验证计划
  -> ApprovalPackage
  -> 外部执行器已发生的回执
  -> VerificationRun
  -> 脱敏、冲突和生命周期质量门
  -> ResearchCase 候选
  -> 发布 / 撤销
```

### C1 Router 契约（已实现）

`RetrievalRouter` 是工具执行和准入的唯一入口：默认先由 LLM Query Planner 仅依据已注册工具名称和能力摘要提出 `tool_rounds`，解析或配置失败则降级到规则规划；无论计划来源，Router 都校验工具白名单、只读属性、Scope、Provider、新鲜度和预算，并记录调用轨迹、拒绝和降级原因。单次请求最多 6 轮、12 次工具调用；预算耗尽或连续没有新增证据时返回已有结果并标记 `partial=true`。成功通道的候选以 RRF（`k=60`）融合，随后最多向 LLM 发送 20 个去重后的最小充分证据片段；LLM 未启用、配置/网络/响应不可信时确定性降级到规则精排。

### 领域契约

| 对象 | 契约 |
| --- | --- |
| `ScopeKey` | 五维隔离键；五个维度均为非空字符串。 |
| `ResearchMemory` | 独立于既有 `MemoryRecord` 的研究记忆；含 `kind`、标题、内容、来源、置信度、适用性、生命周期、时间戳和关联既有记忆 ID。 |
| `EvidenceChunk` | 一次检索返回的最小充分证据，含来源、位置、向量/重排分数、重排理由和元数据。仅为运行时证据对象，当前没有持久化表。 |
| `ResearchCase` | `ResearchMemory` 的案例子类型；在通用字段之外具有 `evidence_chunk_ids`、`proposed_actions` 和 `metadata`。 |
| `ApprovalPackage` | 对案例外部副作用的可审计审批请求；冻结建议内容、验证计划、环境约束和内容哈希，供人工审核。 |
| `VerificationRun` | 外部执行器已经发生的验证回执，包含断言、脱敏日志结果、环境快照和验证状态。 |

修复案例风险门槛保持如下契约：低风险要求验证命令退出码为 0、断言通过且日志已脱敏；中风险还要求集成测试通过；高风险在满足中风险条件后仍需人工明确发布。

### 持久化边界

`ResearchMemory` 使用独立 `research_memory.db`，通过关联 ID 接入既有 `MemoryRecord`，但不扩展、不替换 `MemoryRecord` 表。当前 SQLite 表为：

| 表 | 已持久化内容 |
| --- | --- |
| `research_memories` | `ResearchMemory` 的五维作用域、通用字段和生命周期字段。 |
| `research_memory_links` | `ResearchMemory` 与既有记忆 ID 的有序关联。 |
| `research_audit` | 保存、撤销以及 D1 审批/回执事件的审计记录。 |
| `approval_packages` / `approval_decisions` | 冻结内容哈希、24 小时过期、人工决定及其审计。 |
| `verification_runs` | 已发生外部执行的验证回执；按 `event_key` 幂等保存。 |

`EvidenceChunk` 当前无持久化表。`ResearchCase` 可以以 `kind=research_case` 保存其继承的 `ResearchMemory` 通用字段，但案例专用字段 `evidence_chunk_ids`、`proposed_actions` 和 `metadata` 尚未持久化。D1 已持久化审批包、审批决定和验证回执；它只接收外部执行器已经发生的回执，绝不执行命令。公开研究 API 仍未注册，不能据此宣称公开 API 已交付。

## 模块与文件结构

### 已实现

| 文件 | 职责与 I/O 边界 |
| --- | --- |
| `core/research_models.py` | 定义 `ScopeKey`、研究记忆、证据、案例、审批和验证的纯领域模型；不访问数据库、文件系统或网络。 |
| `core/constants.py` | 定义 ARR 记忆、审批和验证状态枚举；不含业务副作用。 |
| `api/schemas.py` | 定义研究记忆 HTTP 请求/响应 schema 和字段校验；不实现路由或业务逻辑。 |
| `core/config.py` | 提供 `research_memory.db` 默认路径配置。 |
| `memory/research_store.py` | 读写独立 SQLite 中的研究记忆通用字段、关联和审计；输入/输出为 `ResearchMemory` 与 `ScopeKey`，不写目标工作区。 |
| `tests/test_research_models.py` | 验证领域模型、枚举和 HTTP schema 契约。 |
| `tests/test_research_store.py` | 验证 SQLite 隔离、关联、幂等审计、软撤销和迁移。 |

### 计划文件

| 文件 | 阶段 | 职责与 I/O 边界 |
| --- | --- | --- |
| `retrieval/research_retriever.py` | B1 | 仅在 `ScopeKey` 内查询已发布研究记忆和案例，返回可追溯候选。 |
| `retrieval/grep_retriever.py` | B1 | 受路径边界限制的本地 `rg` 与局部读取；拒绝路径逃逸、二进制、密钥模式和未授权来源。 |
| `policy/research_policy.py` | B1 | 判定生命周期、冲突、环境适用性和风险门槛；不执行副作用。 |
| `policy/retrieval_router.py` | C1 | 强制校验计划并调度白名单只读工具，记录预算、轨迹、拒绝和降级原因。 |
| `policy/llm_query_planner.py`、`policy/llm_prompts.py` | C1 | 提供版本化、无密钥的 LLM 工具计划 Prompt 与 SiliconFlow 兼容响应校验；失败时回退规则计划。 |
| `retrieval/bm25_index.py`、`retrieval/bm25_retriever.py` | C1 | 构建 Scope 独立的本地 BM25 索引并返回词法证据。 |
| `retrieval/vector_retriever.py` | C1 | 适配上游 embedding 与向量库，只返回同 Scope、同模型的有效语义证据。 |
| `retrieval/hybrid_retriever.py` | C1 | 归一化候选并做 RRF（`k=60`）与单路确定性排序。 |
| `retrieval/llm_reranker.py` | C1 | 规则精排、受限 SiliconFlow LLM 精排及确定性回退；输入 query 与候选证据，输出排序理由。 |
| `policy/approval_service.py`、`policy/verification_service.py` | D1 | 管理审批包和外部回执验证；不调用外部执行器。 |
| `api/routes_research.py` | D1 | 内部路由编排 schema 与服务层；刻意不注册到公开应用，公开 `/research/...` 请求为 404。 |
| `evaluation/research_eval.py`、`data/gold/research_gold.json` | E1 | 离线评测、金标数据和 E2E 验收输入。 |

## 测试与验收标准

- 单元测试：五维作用域、来源与环境约束、生命周期、RRF、重排输出、脱敏、审批哈希和风险门槛。
- 集成测试：独立 SQLite、关联表、Provider 降级、FastAPI 编排和外部回执输入。
- E2E：科研查询 -> 证据包 -> 待审核案例 -> Fake 回执 -> 研究记忆发布 -> 再召回。
- 评测指标：Recall@K、MRR、适用性准确率、冲突处理准确率、遗忘有效性、Provider 降级率、端到端回收率和延迟分位数。
- 所有测试必须使用 `MockEmbedding`、`MockVectorStore` 和 Fake 后端；不得执行真实命令、写入目标工作区或访问网络。

阶段验收以对应测试通过为准；E1 不得以设计、模型声明或计划文件替代实现证据。D1 的服务与持久化已验收，但公开 API 仍须在真实认证 principal 与 scope 授权完成后单独验收。

## 已完成阶段讲解与验收证据

已完成阶段的整体结构、文件清单和已实现/未实现边界见 [PROJECT_GUIDE.md](PROJECT_GUIDE.md)。本节只记录已完成事实和可复现的验收结果。

### P0 验收讲解

- **目标**：建立可重复运行的 pytest 基线，并恢复 demo JSONL fixture，作为后续 ARR 工作的回归底座。
- **文件**：`.gitignore`、`data/raw/office_demo_events.jsonl`。
- **核心调用流**：pytest 收集现有测试 -> 测试读取 demo JSONL fixture -> 既有功能回归；P0 不引入 ARR 领域对象或检索逻辑。
- **实际测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `202 passed in 0.84s`。
- **限制**：P0 只提供测试基线和 fixture，不代表 B1-E1 的检索、路由、审批、API 或评测已经实现。
- **讲解路径**：[PROJECT_GUIDE.md 的实际已完成范围](PROJECT_GUIDE.md#实际已完成范围)。

### A1 验收讲解

- **目标**：定义 ARR 的领域模型、状态枚举和研究记忆 HTTP schema，使五维隔离和可追溯字段在边界层可校验。
- **文件**：`core/research_models.py`、`core/constants.py`、`api/schemas.py`、`tests/test_research_models.py`。
- **核心字段/调用流**：请求 schema 规范化并校验 `ScopeKey` 的 `team_id`、`project_id`、`repository`、`branch`、`experiment_environment` -> 领域模型承载 `ResearchMemory` 的来源、置信度、适用性和生命周期字段；`EvidenceChunk`、`ResearchCase`、`ApprovalPackage`、`VerificationRun` 在此阶段仅为模型契约。
- **实际测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_models.py tests/test_research_store.py` -> `39 passed in 0.33s`（A1/A2 组合契约验证）；全量回归命令 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `202 passed in 0.84s`。
- **限制**：A1 不提供检索、Router、RRF、重排、审批服务、研究路由或评测实现。
- **讲解路径**：[PROJECT_GUIDE.md 的核心文件说明](PROJECT_GUIDE.md#核心文件说明)。

### A2 验收讲解

- **目标**：实现独立 `ResearchMemory` SQLite 存储、既有记忆关联、审计和软撤销，同时不替换 `MemoryRecord` 表。
- **文件**：`core/config.py`、`memory/research_store.py`、`tests/test_research_store.py`。
- **核心字段/调用流**：`Research
Store.save` 接收带五维 `ScopeKey` 的 `ResearchMemory` -> 写入 `research_memories` 通用字段 -> 写入 `research_memory_links` 有序关联和 `research_audit` 审计 -> `get`、`list_published`、`revoke` 在同一五维作用域内读取或变更；数据库为独立 `research_memory.db`。
- **实际测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_models.py tests/test_research_store.py` -> `39 passed in 0.33s`（A1/A2 组合契约验证）；全量回归命令 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `202 passed in 0.84s`。
- **限制**：`EvidenceChunk` 没有持久化表；`ResearchCase` 仅持久化继承的通用字段，`evidence_chunk_ids`、`proposed_actions` 和 `metadata` 尚未持久化；E1 仍未实现。
- **讲解路径**：[PROJECT_GUIDE.md 的持久化边界](PROJECT_GUIDE.md#持久化边界)。

### B1 验收讲解

- **目标**：在严格五维 `ScopeKey` 边界内召回已发布研究记忆，并安全地从指定本地目录返回可定位的文本证据。
- **文件**：`policy/research_policy.py` 与 `tests/test_research_policy.py`；`retrieval/research_retriever.py` 与 `tests/test_research_retriever.py`；`retrieval/grep_retriever.py` 与 `tests/test_grep_retriever.py`。
- **核心调用流**：调用方传入 `ScopeKey` -> `ResearchRetriever.retrieve` 仅调用 `ResearchStore.list_published(scope)` -> `ResearchPolicy.filter_and_rank` 过滤非发布、作用域/环境不适用项并确定性处理冲突；本地证据路径则由 `GrepRetriever.search` 验证可信根目录与相对路径 -> 跳过链接、二进制和敏感文件/内容 -> 返回带 `source_ref`、`line:<number>` 定位的 `EvidenceChunk`。
- **实际测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest tests\test_research_policy.py tests\test_research_retriever.py tests\test_grep_retriever.py tests\test_research_store.py -q` -> `48 passed, 2 skipped in 0.33s`；全量回归 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `229 passed, 2 skipped in 0.94s`。
- **限制**：B1 本身不提供 C1 的路由、融合或精排能力；当前 Windows 环境未授予创建符号链接权限时，链接边界的两项测试会以 `WinError 1314` 跳过。
- **讲解路径**：[B1 本地证据与研究记忆检索指南](docs/superpowers/b1-local-evidence-retrieval-guide.md)。

### C1 验收讲解

- **目标**：在五维 `ScopeKey` 内完成受控多路检索：LLM 只能提出工具计划，Router 对每次调用强制执行本地、只读、白名单、Provider、新鲜度、Scope 与预算校验。
- **核心调用流**：`LLMQueryPlanner` 提议 -> `RetrievalRouter` 校验或回退 `RuleQueryPlanner` -> `research-memory`、`grep`、`BM25`、`vector` 分路召回 -> `HybridRetriever` 以 RRF（`k=60`）融合 -> 规则精排 -> 可选 SiliconFlow LLM 精排；任意单路、规划或精排失败均保留已有安全结果并记录脱敏降级码。
- **实际专项测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_hybrid_retriever.py tests/test_llm_reranker.py tests/test_llm_prompts.py tests/test_bm25_retriever.py tests/test_vector_retriever.py tests/test_research_retriever.py tests/test_llm_query_planner.py tests/test_retrieval_router.py` -> `180 passed in 0.31s`。
- **实际全量测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `408 passed, 2 skipped in 1.15s`；跳过项仅为 Windows `WinError 1314` 导致无法创建符号链接的两项 `grep` 边界测试。
- **限制**：测试使用 Fake embedding、向量库、HTTP transport 和 LLM client，不访问真实模型或网络；只有环境变量完整、端点精确匹配 `https://api.siliconflow.cn/v1` 时才会尝试 LLM 精排/规划。
- **讲解路径**：[C1 Agentic 路由与重排指南](docs/superpowers/c1-agentic-routing-and-reranking-guide.md)。

### D1 验收讲解

- **目标**：持久化并校验人工审批和外部执行回执，同时保持 ARR 自身非执行。
- **核心调用流**：待审核案例与完整 `ScopeKey` -> `ApprovalService.create_package` 冻结建议、验证计划、环境约束与内容哈希（24 小时有效）-> 非申请人作出人工决定 -> 外部执行器在 ARR 之外执行（未来能力）-> `VerificationService.record_receipt` 校验已批准且未过期的包、案例、哈希、回执证据和 `event_key` 幂等 -> SQLite 与审计记录。服务不调用命令、网络、LLM 或执行子 Agent。
- **公开边界**：`api/routes_research.py` 仅保留内部编排契约，未由 `api/server.py` 注册；所有公开 `/research/...` 请求均为 404。待实现真实认证 principal 与 scope 授权后，才可重新评审并开放公开接口。
- **实际 D1 专项测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_models.py tests/test_research_store.py tests/test_approval_service.py tests/test_verification_service.py tests/test_routes_research.py` -> `63 passed`。
- **实际全量测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `477 passed, 2 skipped in 2.13s`；两项跳过是 Windows 无创建符号链接权限（`WinError 1314`）导致的既有 `grep` 边界测试。

### E1 验收讲解

- **目标**：提供严格金标校验的离线排序评测、不会执行副作用的 Fake 回执 E2E，以及面向使用者的中文操作与安全边界说明。
- **核心调用流**：`data/gold/research_gold.json` -> `load_research_gold` 校验完整 Scope、候选和相关 ID -> `evaluate_ranking` 计算 `Recall@K`、`MRR` 与 `scope_leak_count`；另一条离线流为 `ResearchCase` -> 审批包 -> 人工决定 -> Fake 回执 -> `VerificationRun` 与脱敏审计记录，流程不执行计划且不自动发布研究记忆。

```text
research_gold.json
        -> 严格金标校验
        -> 排序结果
        -> Recall@K / MRR / scope_leak_count

ResearchCase -> 审批包 -> 人工决定 -> Fake 回执
                                      -> VerificationRun + 审计
                                      -X-> 不执行计划 / 不自动发布

公开 /research/... -> 404
    （待真实认证 principal + scope 授权后重新评审）
```

- **文件职责**：`evaluation/research_eval.py` 负责金标加载、指标计算和离线编排；`data/gold/research_gold.json` 提供脱敏金标；`tests/test_research_eval.py` 固化指标、Fake 回执、公开 404 与用户文档契约；`docs/user-guide.md` 提供可复制命令、指标解释和边界说明。
- **指标解释**：`Recall@K` 只检查前 K 个结果对相关证据的覆盖；`MRR` 是第一个相关结果的倒数排名；`scope_leak_count` 统计完整返回序列内 Scope 不匹配的候选数。
- **公开与非执行边界**：Fake 回执只是 ARR 接收的离线输入，ARR 不调用命令、网络、远程 LLM 或执行子 Agent，不写目标工作区，也不自动发布；`api/server.py` 未注册 research router，因此公开 `/research/...` 一律为 404，必须先接入真实认证 principal 和 scope 授权。
- **RAGAS 状态**：当前不启用；现有金标没有经过人工标注的回答、上下文、参考答案和稳定裁判模型配置。未来须具备脱敏版本化样本、可离线复现的评测模型/提示词、许可/隐私/成本/网络评审，以及独立阈值和回归测试后才可引入。
- **实际专项测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_eval.py` -> `22 passed in 0.40s`。
- **实际全量测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `499 passed, 2 skipped in 2.21s`；两项跳过均是 Windows 无创建符号链接权限（`WinError 1314`）导致的既有 `grep` 边界测试。
- **Task 4 真实模型边界**：真实运行仅由 `scripts/run_live_llm_eval.py` 的显式手工命令触发；每轮 Planner/Reranker 共用最多 20 次真实调用预算，报告仅保留脱敏统计，绝不进入 pytest 或 CI。
- **Task 4 受控真实运行**：同一 10 例数据集的 `arr-router-v1` 为通过 4、规划 10、精排 10、降级 6、timeout 4、invalid 2、Scope 泄漏 0；`arr-router-v2` 为通过 7、规划 10、精排 9、降级 3、timeout 2、invalid 0、Scope 泄漏 0。v2 保持零 Scope 泄漏并改善降级/invalid，但仍存 2 个 timeout。
- **Task 4 离线专项测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest tests/test_live_llm_eval.py tests/test_llm_prompts.py tests/test_llm_query_planner.py tests/test_llm_reranker.py tests/test_retrieval_router.py -q` -> `173 passed in 0.25s`。
- **Task 4 实际全量测试结果**：`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `564 passed, 2 skipped in 2.55s`；跳过项仍仅为 Windows `WinError 1314` 的既有 `grep` 符号链接边界测试。
- **讲解路径**：[E1 离线评测与回执用户指南](docs/user-guide.md)。

## 实施排期与依赖

| 阶段 | 状态 | 依赖 | 交付与验收 |
| --- | --- | --- | --- |
| P0 | ✅ 已完成 [] | 无 | pytest 基线和 demo fixture 可用。 |
| A1 | ✅ 已完成 [] | P0 | 领域模型、枚举、schema 及其测试通过。 |
| A2 | ✅ 已完成 [] | A1 | 独立 SQLite、关联和审计测试通过。 |
| B1 | ✅ 已完成 [x] | A2 | 本地证据与研究记忆检索；路径边界、生命周期、适用性和冲突测试通过。 |
| C1 | ✅ 已完成 [x] | B1 | Agentic Router、BM25/vector/ResearchMemory 召回、RRF、规则/受限 LLM 精排、预算、轨迹与降级已通过专项和全量回归。 |
| D1 | ✅ 已完成 [x] | A2、C1 | 审批、回执验证、持久化和内部路由契约已验收；公开 `/research/...` API 未注册，保持 404，待 principal 与 scope 授权后再开放。 |
| E1 | ✅ 已完成 [x] | D1 | 离线评测、Fake 回执 E2E 和中文用户文档已验收；专项 `22 passed`，全量 `499 passed, 2 skipped`。 |

## 变更记录

后续变更必须记录阶段、摘要、验证命令和讲解路径；不得把计划项写为已完成事实。

| 日期 | 阶段 | 摘要 | 验证命令 | 讲解路径 |
| --- | --- | --- | --- |
| 2026-07-28 | P0 | 记录 pytest 基线和 demo JSONL fixture 的已完成验收事实。 | `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` | [P0 验收讲解](#p0-验收讲解) |
| 2026-07-28 | A1 | 记录领域模型、枚举和 HTTP schema 的已完成验收事实。 | `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_models.py tests/test_research_store.py` | [A1 验收讲解](#a1-验收讲解) |
| 2026-07-28 | A2 | 记录独立 SQLite 存储、关联、审计和软撤销的已完成验收事实。 | `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_models.py tests/test_research_store.py` | [A2 验收讲解](#a2-验收讲解) |
| 2026-07-28 | B1 | 完成本地证据与研究记忆检索、路径安全和策略过滤的验收记录。 | `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest tests\test_research_policy.py tests\test_research_retriever.py tests\test_grep_retriever.py tests\test_research_store.py -q` -> `48 passed, 2 skipped`；`C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` -> `229 passed, 2 skipped` | [B1 验收讲解](#b1-验收讲解) |
| 2026-07-29 | C1 | 完成 Agentic Router、BM25/vector/ResearchMemory 召回、RRF、规则/受限 LLM 精排、预算、轨迹与降级收尾。 | `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_hybrid_retriever.py tests/test_llm_reranker.py tests/test_llm_prompts.py tests/test_bm25_retriever.py tests/test_vector_retriever.py tests/test_research_retriever.py tests/test_llm_query_planner.py tests/test_retrieval_router.py` -> `180 passed in 0.31s`；全量 -> `408 passed, 2 skipped in 1.15s` | [C1 Agentic 路由与重排指南](docs/superpowers/c1-agentic-routing-and-reranking-guide.md) |
| 2026-07-29 | D1 | 完成审批包/决定与验证回执的同库持久化、服务校验和内部路由契约；公开路由刻意未注册，等待真实 principal 与 scope 授权。 | `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_models.py tests/test_research_store.py tests/test_approval_service.py tests/test_verification_service.py tests/test_routes_research.py` -> `63 passed`；全量 -> `477 passed, 2 skipped in 2.13s` | [D1 审批、验证回执与公开边界](docs/superpowers/plans/2026-07-27-arr-project-guide-documentation-implementation.md#d1审批验证回执与公开边界) |
| 2026-07-30 | E1 | 完成离线金标评测、Fake 回执 E2E、中文用户指南和受控真实 LLM Prompt v1/v2 基准；真实调用为显式手工运行、每轮最多 20 次，始终不进入 pytest/CI。 | Task 4 专项 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest tests/test_live_llm_eval.py tests/test_llm_prompts.py tests/test_llm_query_planner.py tests/test_llm_reranker.py tests/test_retrieval_router.py -q` -> `173 passed in 0.25s`；全量 -> `564 passed, 2 skipped in 2.55s` | [E1 验收讲解](#e1-验收讲解) |
| 2026-07-28 | 文档规格 | 重构阶段描述，明确 C1 的目标契约并固化当前持久化边界。 | `git diff --check -- DEV_SPEC.md` | [技术架构与数据模型](#技术架构与数据模型) |

## 未来范围

- 远程 LLM、`WebSearchProvider`、真实外部执行器和跨项目检索不属于 V1。
- 与既有比赛功能的 UI 扩展、跨课题组共享和新索引后端，在 V1 验收后单独立项。
- 对 `EvidenceChunk`、`ResearchCase` 案例专用字段的持久化，以及公开 research API 的真实 principal 与 scope 授权，必须在相应后续阶段设计迁移、审计与测试后再实施。

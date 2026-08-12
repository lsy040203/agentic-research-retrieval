# ARR 项目指南

## 项目定位

ARR（Agentic Research Retrieval）是 OS Agent Memory 中面向科研工作流的研究记忆与检索决策子系统。它服务于文献调研、研究计划、实验、数据分析、科研代码和论文写作，在课题组、项目、仓库、分支与实验环境共同组成的隔离范围内组织可追溯的研究记忆。

本子系统独立于既有通用 `MemoryRecord` 存储：研究记忆保存在专用 SQLite 数据库中，只通过关联 ID 与既有记忆发生联系，不替换原有记忆表。

## 实际已完成范围

当前实际完成的工作覆盖 P0、A1、A2、B1、C1、D1 和 E1。

- P0：建立 pytest 测试基线，并恢复演示 JSONL fixture。
- A1：定义 ARR 领域模型、状态枚举与 HTTP schema；覆盖作用域隔离、来源与环境字段、生命周期及模型校验。
- A2：实现独立的 `ResearchMemory` SQLite 存储、与既有记忆的关联表、审计记录及撤销（软删除）行为。
- B1：实现五维 Scope 隔离下的已发布研究记忆召回和可信目录内本地文本证据检索。
- C1：实现受控 Agentic Router、BM25/vector/ResearchMemory 多路召回、RRF 融合、规则/受限 LLM 精排、轨迹和确定性降级。
- D1：实现审批包、人工决定、外部验证回执的持久化与内部路由契约；公开 `/research/...` 保持 404。
- E1：实现离线金标排序评测、Fake 回执 E2E 和中文用户指南；专项回归为 `22 passed`，全量回归为 `499 passed, 2 skipped`。

公开 research API、真实认证 principal 与 scope 授权、真实外部执行器和 RAGAS 仍是未来扩展，不属于已交付范围。

## 已完成架构

```text
API schema validation
        |
        v
ScopeKey: five-dimensional scope
  team_id + project_id + repository + branch + experiment_environment
        |
        +--> A1 domain models
        |      |- ResearchMemory
        |      |- EvidenceChunk       (no persistence table)
        |      |- ResearchCase        (ResearchMemory subclass)
        |      |- ApprovalPackage
        |      `- VerificationRun
        |
        v
ResearchStore (A2)
        |
        +--> research_memories        (ResearchMemory base fields)
        +--> research_memory_links    (links to existing MemoryRecord IDs)
        `--> research_audit           (memory_id + event_key audit entries)
        |
        v
research_memory.db (separate SQLite database)

已完成的后续层：
ResearchMemory / 本地证据
        -> C1 Router（只读白名单、RRF、精排与降级）
        -> D1 审批包 / 人工决定 / Fake 或外部回执记录
        -> E1 离线金标评测与用户指南
        -X-> 不执行计划、不自动发布、公开 /research/... 为 404
```

## 聚焦文件树

```text
os_agent_memory/
|- core/
|  |- research_models.py
|  |- constants.py
|  `- config.py
|- api/
|  |- schemas.py
|  `- routes_research.py
|- memory/
|  `- research_store.py
|- policy/
|  |- retrieval_router.py
|  |- approval_service.py
|  `- verification_service.py
|- retrieval/
|  |- bm25_retriever.py
|  |- vector_retriever.py
|  `- hybrid_retriever.py
|- evaluation/
|  `- research_eval.py
|- data/gold/
|  `- research_gold.json
|- docs/
|  `- user-guide.md
`- tests/
   |- test_research_models.py
   |- test_research_store.py
   `- test_research_eval.py
```

## 已完成数据流

```text
Caller / API
    |
    v
Schema and ScopeKey validation
    |
    v
ResearchMemory or ResearchCase
    |
    v
ResearchStore.save / get / list_published / revoke / append_audit
    |
    +--> scope-filtered SQL operations
    +--> transaction commit or rollback
    +--> explicit connection close
    |
    v
research_memory.db
    |
    +--> memory links to existing MemoryRecord IDs
    `--> idempotent audit trail
```

## 核心文件说明

| 文件 | 已完成职责 |
|---|---|
| `core/research_models.py` | 定义不可变的 `ScopeKey`、`ResearchMemory`、`EvidenceChunk`、`ResearchCase`、审批包与验证回执模型；`EvidenceChunk` 校验分数和 JSON 元数据。 |
| `core/constants.py` | 提供研究记忆类型、研究记忆状态、审批状态和验证状态等 ARR 枚举。 |
| `core/config.py` | 定义默认专用数据库路径，文件名为 `research_memory.db`。 |
| `api/schemas.py` | 提供研究记忆响应等 API schema，并对作用域和状态契约进行校验。 |
| `memory/research_store.py` | 提供独立 SQLite 初始化、研究记忆保存与读取、已发布列表、关联 ID 查询、撤销和审计追加；包含旧审计表迁移。 |
| `policy/retrieval_router.py` | 对检索计划执行白名单、只读、Scope、Provider、新鲜度和预算校验，编排多路召回、融合、精排与降级。 |
| `policy/approval_service.py`、`policy/verification_service.py` | 冻结审批包、记录人工决定并校验已发生的外部回执；不调用执行器。 |
| `evaluation/research_eval.py` | 严格加载离线金标，计算 Recall@K、MRR 和 `scope_leak_count`，编排 Fake 回执 E2E。 |
| `docs/user-guide.md` | 提供 E1 命令、指标解释、Fake 回执非执行/非发布及公开 404 边界。 |
| `tests/test_research_models.py` | 覆盖五维作用域、模型可变默认值隔离、枚举、证据 JSON/分数校验、案例模型及 schema 契约。 |
| `tests/test_research_store.py` | 覆盖独立存储、五维隔离、关联、撤销、审计幂等、UTC 时间、迁移回滚和连接关闭。 |

## 持久化边界

`ResearchCase` 是 `ResearchMemory` 的子类。因此它的父类通用字段可以按 `ResearchMemory` 写入和读取，包括标识、五维作用域、类型、标题、内容、来源引用、置信度、适用性、状态、创建与更新时间及关联记忆 ID。

`ResearchCase` 的案例专用字段 `evidence_chunk_ids`、`proposed_actions` 和 `metadata` 当前没有持久化。`EvidenceChunk` 也只有领域模型和序列化/校验逻辑，当前没有对应的持久化表。二者都不能被表述为已存储或已检索的实体。

## 当前保证

- 五维作用域：所有读取和变更均以 `team_id`、`project_id`、`repository`、`branch`、`experiment_environment` 精确过滤，避免跨作用域访问。
- 独立数据库：研究记忆使用专用 `research_memory.db`，不改写既有通用记忆表。
- 参数化 SQL、事务与关闭：数据库操作使用参数化 SQL；初始化和写入具有提交/回滚边界；公共操作及异常路径均关闭 SQLite 连接。
- UTC 时间：写入时将带偏移时间归一化为 UTC，旧审计时间迁移时也转为 UTC，排序以 UTC 时刻为准。
- 幂等审计：审计以同一 `memory_id` 与 `event_key` 去重；重复事件不会重复写入。重复撤销不会改变已撤销记录的时间或新增审计。
- C1 受控检索：LLM 只能提出计划，不能越过 Router 的白名单、只读、Scope、Provider、新鲜度和预算门禁；失败时回退到确定性规则结果。
- D1 非执行与公开边界：审批和回执服务只记录人工决定及已发生的回执；ARR 不执行命令、不自动发布，公开 `/research/...` 仍为 404。
- E1 离线评测：`research_gold.json` 驱动 Recall@K、MRR 和 `scope_leak_count`；Fake 回执 E2E 只写验证和审计记录。

## 已完成阶段状态与未来扩展

```text
P0 -> A1 -> A2 -> B1 -> C1 -> D1 -> E1
done   done  done  done  done  done  done
```

未来扩展标识保持如下，均不属于上述已完成阶段：

- **未来**：接入真实认证 principal 和逐请求 scope 授权后，重新评审是否开放公开 `/research/...` API。
- **未来**：受控外部执行器、真实发布工作流、远程 LLM、`WebSearchProvider`、跨项目检索与 RAGAS；每项都需要独立的安全设计、审计和测试。

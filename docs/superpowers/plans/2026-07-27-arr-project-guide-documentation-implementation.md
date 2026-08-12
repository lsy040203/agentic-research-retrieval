# ARR 项目讲解文档实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用中文补充 ARR 已实现 P0/A1/A2/B1 的架构图、文件结构和项目讲解，并清晰区分未完成的 C1-E1。

**Architecture:** `DEV_SPEC.md` 提供与规格紧邻的简明 ASCII 总览图；根目录 `PROJECT_GUIDE.md` 提供面向评审与开发者的详细说明。两份文档均以当前源码和测试为证据，不修改业务代码和任务状态。

**Tech Stack:** Markdown、ASCII 图、现有 Python 源码与 pytest 验证结果。

---

## 已实现阶段讲解（P0、A1、A2、B1）

当前已实现的范围是“科研记忆的定义、安全存储与本地只读检索”。B1 已提供研究记忆召回和可信目录内的文本证据搜索；完整的 Agentic 决策、候选融合、重排、审批和 API 系统仍未完成。

## ARR 全链路架构图（A → B → C → D → E）

下图将 A 至 E 阶段放到同一条科研 Agent 工作链路中。主线从“请求与数据”开始，经检索、规划和重排生成**待审批建议**；D 阶段只接收人工已经批准后的外部执行回执；E 阶段独立评估链路质量。它不是“LLM 自动修改代码”的流程图：LLM 只能提出工具规划或重排建议，不能越过 Scope、审批和验证门禁。

```text
┌────────────────────────────────── 使用方 / 科研 Agent ──────────────────────────────────┐
│  任务：文献调研、实验设计、实验失败诊断、数据分析、科研代码开发、论文写作等            │
│  输入：query + 完整五维 ScopeKey + 可选任务上下文                                        │
└─────────────────────────────────────────────┬───────────────────────────────────────────┘
                                              │
                                              v
┌────────────────────── A：研究记忆基础、契约与安全存储 ──────────────────────┐
│ api/schemas.py / core/research_models.py / core/constants.py                 │
│ ScopeKey = team + project + repository + branch + experiment_environment      │
│ ResearchMemory / EvidenceChunk / ResearchCase / 生命周期、置信度、来源、适用条件 │
│                                      │                                        │
│                                      v                                        │
│ memory/research_store.py  ──> research_memory.db                              │
│  精确 Scope 隔离、SQLite 事务、关联、软删除、幂等审计                          │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ 只读候选与可追溯元数据
                                       v
┌────────────────────────── B：本地证据与研究记忆召回 ─────────────────────────┐
│ ResearchRetriever /token覆盖率检索───────────────> 已发布 ResearchMemory                      │
│ GrepRetriever / 本地文本检索 ────> 允许目录中的文件级 EvidenceChunk             │
│ ResearchPolicy：生命周期、适用条件、冲突和路径边界过滤                          │
│ 输出：每路候选（内容、来源、定位、Scope、分数、适用条件）                       │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ 多路候选，仍保留来源与 Scope
                                       v
┌──────────────────── C：Agentic 路由、混合检索、融合与精排 ────────────────────┐
│ LLMQueryPlanner ──提议──> 工具顺序 / query 改写 / 轮次                         │
│                          │                                                     │
│ RetrievalRouter <────────┘ 强制校验：白名单、Scope、索引新鲜度、6 轮/12 次预算 │
│     │                    │                                                     │
│     ├─ BM25Retriever     ├─ VectorRetriever（调用上游麒麟 embedding / 向量索引）│
│     ├─ ResearchMemory    └─ GrepRetriever                                     │
│     │                                                                          │
│     └─> RRF 融合 ─> RuleReranker ─> 可选 LLMReranker ─> 带理由的排序证据        │
│                              │                │                               │
│                              │                └─超时 / 非法响应：回退规则排序 │
│                              └─ rule / llm / hybrid 三种模式                  │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ 证据充足：返回可追溯答案依据
                                       │ 证据不足：只生成待审核修复/实验建议
                                       v
┌──────────────────── D：人工审批、外部子 Agent 与验证回执 ─────────────────────┐
│ ResearchCase ─> ApprovalService ─> ApprovalPackage（哈希、风险、24h 有效期）    │
│                                      │                                         │
│                         人工批准 / 拒绝│（申请人与审批人分离）                  │
│                                      v                                         │
│               外部执行子 Agent / 执行器（由外部系统启动，ARR 不直接执行）       │
│                                      │ 仅返回执行与验证结果                    │
│                                      v                                         │
│ VerificationService ─> VerificationRun + 脱敏审计 + 候选 ResearchMemory        │
│   校验审批状态、案例哈希、环境、证据、事件幂等；拒绝 token / API key 等敏感内容  │
│   公开 /research/* 暂为 404，待真实 principal 认证与 Scope 授权后再开放         │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ 仅验证成功、满足质量门的候选可进入治理
                                       v
┌────────────────────── E：离线验收、真实 LLM 基准与反馈 ───────────────────────┐
│ research_gold.json ─> research_eval.py ─> Recall@K / MRR / scope_leak_count   │
│       │                          │                                              │
│       │                          └─> Fake 回执 E2E（不执行计划，不写目标工作区）│
│       │                                                                         │
│ llm_live_eval.json ─> run_live_llm_eval.py ─> Prompt 版本 / 20 次共享调用预算 │
│       │                          │                                              │
│       │                          └─> 脱敏报告 data/eval_reports/（Git 忽略）    │
│       └─ pytest / CI 永远用 Fake client；真实 LLM 仅允许手工显式运行             │
└──────────────────────────────────────────────────────────────────────────────┘

贯穿 A-E 的硬约束：完整 ScopeKey 隔离、来源可追溯、生命周期治理、审计脱敏、
最小权限、确定性回退，以及“ARR 不直接执行命令、不修改目标工作区、不自动发布记忆”。
```

### 如何阅读这张总图

- **A 是可信数据底座**：没有完整 `ScopeKey` 的请求无法进入后续任何模块；`ResearchStore` 负责持久化和审计，但本身不是检索器。
- **B 与 C 分工**：B 负责从具体来源拿到安全候选；C 决定哪些来源组合、如何融合和怎样排序。`ResearchMemory` 是一类已验证的记忆数据，不是一种由 LLM 临时生成的“错题本”。
- **D 是人类控制门**：C 的输出最多是建议。审批通过后，外部子 Agent 才能执行；ARR 只验证其回执并记录结果，不替它运行命令。
- **E 是旁路验收，不是在线执行环节**：离线测试验证指标和安全边界，真实 LLM 基准只用于受控 Prompt 比较；两者都不能触发真实工作区修改。

### 已交付架构图（P0、A1、A2）

```text
P0：pytest 基线与可复现 demo JSONL fixture

                    ┌─────────────────────────────────────┐
                    │ A1：输入契约（尚未接入实际路由）      │
                    │ ResearchScopeSchema / 请求响应模型   │
                    └────────────────┬────────────────────┘
                                     │ 校验五维作用域
                                     v
┌─────────────────────────────────────────────────────────────────┐
│ ScopeKey                                                         │
│ team + project + repository + branch + experiment_environment    │
└──────────────┬──────────────────────────────────────────────────┘
               │ 作用域隔离
               v
┌──────────────────────── A1：领域层 ────────────────────────────┐
│ ResearchMemory：科研记忆通用实体                                 │
│ ├─ EvidenceChunk：证据片段模型（仅内存模型，未持久化）           │
│ ├─ ResearchCase：记忆子类（案例专属字段未持久化）                │
│ ├─ ApprovalPackage / VerificationRun：先定义模型，服务未实现     │
│ └─ 枚举：类型、生命周期、审批/验证状态                           │
└─────────────────────────┬───────────────────────────────────────┘
                          │ save / get / list / revoke / audit
                          v
┌──────────────────────── A2：持久化层 ──────────────────────────┐
│ ResearchStore（Python 存储类，而非数据库本身）                    │
│ ├─ 精确五维作用域过滤                                            │
│ ├─ UTC 时间标准化                                                │
│ └─ 审计事件按 (memory_id, event_key) 幂等                        │
└───────────────┬────────────────────┬────────────────────────────┘
                v                    v
     research_memories      research_memory_links
     通用字段与状态          关联到既有 MemoryRecord 的 ID
                │
                v
          research_audit
          撤销与业务事件审计

已完成：B1 本地证据与研究记忆检索
计划中、尚未实现：C1 Agentic 决策/重排 → D1 审批/API → E1 评测
```

### 一条科研记忆表达什么

```text
在什么团队 / 项目 / 仓库 / 分支 / 实验环境下，
记录什么科研知识；它来自哪里、可信度如何、适用于什么条件；
随后安全地保存、关联、审计和撤销。
```

### 关键领域字段

| 对象或字段 | 含义与作用 |
|---|---|
| `ScopeKey` | 五维隔离键，包含 `team_id`、`project_id`、`repository`、`branch`、`experiment_environment`。任一维度不匹配时，读取、关联查询、撤销与审计均不得跨作用域进行。 |
| `ResearchMemory.memory_id` | 一条科研记忆的唯一标识。 |
| `ResearchMemory.kind` | 记忆类别，例如知识、工作流或研究案例。 |
| `title` / `content` | 记忆的简短标题与核心内容。 |
| `source_refs` | 来源引用，例如论文、实验记录或文件位置。 |
| `confidence` | 0 至 1 的置信度。 |
| `applicability` | 适用条件，例如数据集、模型版本或硬件环境。 |
| `status` | 生命周期状态，例如候选、已验证、已发布、撤销。 |
| `related_memory_ids` | 与既有 `MemoryRecord` 的关联 ID；不会改写旧记忆表。 |
| `created_at` / `updated_at` | 创建和更新时间；写入 SQLite 时规范化为 UTC。 |

`EvidenceChunk` 表示可追溯的原始证据片段，包含来源、定位信息、未来检索分数与重排理由；目前没有专用持久化表。

`ResearchCase` 表示实验失败、依赖冲突或可复用工作流等案例。它继承 `ResearchMemory`，因此可保存其通用字段；`evidence_chunk_ids`、`proposed_actions` 与案例 `metadata` 尚未持久化。

`ApprovalPackage` 和 `VerificationRun` 已定义为领域数据结构，分别用于审批申请和外部执行回执；对应服务与 API 属于未完成的 D1。

### 已实现文件及职责

| 文件 | 作用 |
|---|---|
| `core/research_models.py` | 定义五维作用域、研究记忆、证据、案例、审批包和验证回执。 |
| `core/constants.py` | 定义研究记忆、审批和验证的类型与状态枚举。 |
| `api/schemas.py` | 定义研究记忆 HTTP 入参/出参；裁剪作用域字段空格并拒绝空值。 |
| `core/config.py` | 定义独立数据库默认路径，文件名为 `research_memory.db`。 |
| `memory/research_store.py` | 实现 SQLite 初始化、保存、读取、已发布列表、关联查询、撤销和审计。 |
| `tests/test_research_models.py` | 验证领域模型、作用域、JSON 安全性、状态与 schema 契约。 |
| `tests/test_research_store.py` | 验证五维隔离、事务、审计幂等、UTC 时间、迁移回滚和连接关闭。 |

### 当前数据流

```text
调用方 / HTTP 请求
        |
        v
Schema 与 ScopeKey 校验
        |
        v
ResearchMemory 或 ResearchCase 的通用字段
        |
        v
ResearchStore.save / get / list_published / revoke / append_audit
        |
        +-- 精确五维 SQL 过滤
        +-- 事务提交或回滚
        +-- 显式关闭连接
        v
research_memory.db
        +-- research_memories
        +-- research_memory_links -> 既有 MemoryRecord ID
        `-- research_audit（memory_id + event_key 去重）
```

### 已实现保证与未实现边界

- 所有研究记忆读取与变更均必须传入完整 `ScopeKey`。
- 研究记忆使用独立数据库，不读写既有通用 `MemoryRecord` 存储。
- SQL 参数化执行；数据库初始化和写入使用事务；正常与异常路径均关闭连接。
- 审计事件以 `memory_id` 与 `event_key` 去重；重复撤销不重复更新或写入审计。
- B1 本地检索、C1 Agentic Router 与重排、D1 审批服务/API、E1 评测与 E2E 均未实现。

### 架构术语与责任边界

`api/schemas.py` 中的 `ResearchScopeSchema`、创建请求、读取响应和状态更新 schema 是 HTTP 输入/输出契约：它们规定调用者能够提交哪些字段、字段如何校验，以及服务未来返回的数据形状。当前阶段这些 schema 已定义，但尚未接入实际的 `/research/...` 路由；现有 `api/server.py` 也未注册 research router。因此它们不是已可调用的科研记忆 REST API，路由与审批 API 属于后续 D1 的交付范围。

领域对象之间应按下列关系理解，而不能把所有对象都视为 `ResearchMemory` 的组成部分：

```text
ResearchMemory                         科研记忆的通用主体
└── ResearchCase                        一种特殊科研记忆（子类）

EvidenceChunk                           独立的可追溯证据片段，不是 ResearchMemory 子类
ApprovalPackage                         独立的审批申请，以 case_memory_id 指向案例
VerificationRun                         独立的验证回执，以 case_memory_id 指向案例
```

这里的“持久化”是指将 Python 运行内存中的对象序列化并写入磁盘上的 SQLite 文件，使进程结束后数据仍可恢复。A2 当前持久化的是 `ResearchMemory` 的通用字段、它与既有 `MemoryRecord` 的关联以及审计记录；`EvidenceChunk` 没有持久化表，`ResearchCase` 的 `evidence_chunk_ids`、`proposed_actions` 和 `metadata` 也还不会随案例写入数据库。

`ResearchStore` 是操作数据库的 Python 存储类，不是数据库文件本身。它将 `save`、`get`、`list_published`、`list_by_related_memory_id`、`revoke` 和 `append_audit` 等业务操作转换为 SQLite 查询和事务；实际数据库是 `memory_store/research_memory.db`。三个已实现数据表各自承担不同职责：

| 表 | 存放内容 | 在当前阶段的用途 |
|---|---|---|
| `research_memories` | 研究记忆通用字段、五维作用域、状态和 UTC 时间 | 研究记忆的主记录；支持精确作用域读取和已发布列表。 |
| `research_memory_links` | `ResearchMemory` 到既有 `MemoryRecord` ID 的关联及顺序 | 将研究记忆连接到旧记忆，不复制或改写旧记忆数据。 |
| `research_audit` | 操作、事件键、详情和发生时间 | 记录撤销等审计事件；以 `(memory_id, event_key)` 去重，支持重复调用不产生重复记录。 |

`ResearchStore` 当前提供的是存储、状态与关联查询能力，不是检索器。B1 由独立的 `ResearchRetriever` 和 `GrepRetriever` 分别完成已发布记忆召回与本地文本搜索；向量检索、RRF 融合和重排仍属于 C1。

## B1：本地证据与研究记忆检索

B1 在完整五维 `ScopeKey` 范围内完成两条只读检索路径：一条从独立研究记忆库中召回已发布的 `ResearchMemory`，另一条从构造时指定的可信本地目录返回行级 `EvidenceChunk`。B1 不写入目标工作区、不执行命令、不访问网络；向量检索、BM25、RRF 融合、Agentic Router 与 API 都不属于 B1。

### B 阶段架构图（B1）

```text
                           B1：本地证据与研究记忆检索

 ┌──────────────────────────────────────────────────────────────┐
 │ 输入                                                         │
 │ ├─ 研究记忆路径：ScopeKey                                   │
 │ └─ 本地文本路径：query + relative_path + 可信 root          │
 └───────────────────────┬───────────────────────┬──────────────┘
                         │                       │
                         v                       v
       ┌──────────────────────────┐  ┌──────────────────────────┐
       │ ResearchRetriever        │  │ GrepRetriever            │
       │ retrieve(scope)          │  │ search(query, path)      │
       └────────────┬─────────────┘  └────────────┬─────────────┘
                    │                             │
                    v                             v
       ┌──────────────────────────┐  ┌──────────────────────────┐
       │ ResearchStore            │  │ 路径与文件安全边界        │
       │ list_published(scope)    │  │ 拒绝逃逸/链接/敏感/二进制 │
       └────────────┬─────────────┘  │ 限制文件数、大小和结果数  │
                    │                └────────────┬─────────────┘
                    v                             │
       ┌──────────────────────────┐               v
       │ ResearchPolicy           │  ┌──────────────────────────┐
       │ 五维隔离、生命周期、环境 │  │ casefold 逐行匹配         │
       │ 适用性、冲突确定性消解   │  └────────────┬─────────────┘
       └────────────┬─────────────┘               │
                    │                             │
                    v                             v
 ┌───────────────────────────────┐ ┌────────────────────────────┐
 │ 可用 ResearchMemory 列表       │ │ EvidenceChunk              │
 │ 已发布且适用于当前 ScopeKey    │ │ 相对路径 + 行号 + 查询元数据 │
 └───────────────────────────────┘ └────────────────────────────┘

 C1 未实现：BM25 / 向量检索 / RRF 融合 / 本地重排 / Agentic Router
```

### 文件结构与职责

| 文件 | 职责 | 输入与输出 |
|---|---|---|
| `retrieval/research_retriever.py` | 将存储层与策略层解耦；自身不直接查询 SQLite。 | 输入 `ScopeKey`；调用 `list_published(scope)`，输出策略认可的 `ResearchMemory` 列表。 |
| `policy/research_policy.py` | 无副作用地执行五维隔离、发布状态、环境适用性过滤和冲突消解，并计算检索置信度。 | 输入作用域和候选记忆；输出可用记忆及用于判定的分数。 |
| `retrieval/grep_retriever.py` | 在可信根目录内用纯 Python 做大小写无关的逐行匹配，建立可定位的本地证据。 | 输入查询词和相对路径；输出 `EvidenceChunk` 列表。 |
| `tests/test_research_retriever.py` | 验证只调用 `list_published`、完整委托给策略层，且不接受非发布或跨作用域记忆。 | 存储/策略替身和临时 SQLite。 |
| `tests/test_research_policy.py` | 验证五维隔离、环境条件、冲突决胜、确定性置信度与不修改输入对象。 | 构造的 `ResearchMemory` 候选。 |
| `tests/test_grep_retriever.py` | 验证根目录边界、符号链接、二进制/敏感内容跳过、结果/文件/字节上限及无子进程。 | 临时可信目录和受控文件。 |

### B1 检索架构图

```text
                                  B1：两条只读检索路径

路径 A：已发布研究记忆
调用方 + ScopeKey
        |
        v
ResearchRetriever.retrieve(scope)
        |
        v
ResearchStore.list_published(scope)       # A2：SQLite 精确五维读取
        |
        v
ResearchPolicy.filter_and_rank(scope, candidates)
        |
        +-- ScopeKey 是否五维完全相等？否 -> 丢弃
        +-- status 是否 PUBLISHED？       否 -> 丢弃
        +-- experiment_environments 是否适用？否 -> 丢弃
        `-- 同一非空 conflict_key 仅保留确定性优胜项
        |
        v
可用 ResearchMemory 列表

路径 B：本地文本证据
query + relative_path
        |
        v
GrepRetriever（构造时固定可信 root）
        |
        +-- 拒绝空查询、绝对路径、根目录逃逸、路径中的符号链接
        +-- 跳过符号链接、非普通文件、二进制、非 UTF-8、超限文件
        +-- 跳过 .env / credentials 等敏感路径及密钥、Token、Authorization 内容
        |
        v
逐行 casefold 匹配 -> EvidenceChunk
        source_ref = POSIX 相对路径
        locator    = line:<行号>
        metadata   = {retriever: python-grep, query: <查询词>}
```

### 核心字段、策略与公式

研究记忆路径的最小输入是 `ScopeKey`。`ResearchPolicy` 首先要求候选的五个作用域字段都与查询作用域相等，且 `status == PUBLISHED`。`applicability.experiment_environments` 缺失时表示不额外限制环境；存在时必须是列表且包含当前 `experiment_environment`。

策略会读取以下 `ResearchMemory` 字段：`source_refs` 用于来源可靠性，`status` 用于发布状态，`updated_at` 用于新鲜度，原始 `confidence` 用于冲突决胜；`applicability` 可包含 `experiment_environments`、`conflict_key`、`locator`、`verification_log` 与 `experiment_id`。其中 `conflict_key` 只有非空白字符串才形成冲突组；同组按“原始置信度更高 -> UTC 更新时间更晚 -> memory_id 字典序更小 -> 检索分数更高”确定唯一优胜项。该方法名为 `filter_and_rank`，但当前实现不按检索分数对所有结果做全局排序；它保留未分组项的输入顺序，再追加各冲突组的优胜项。

检索置信度被裁剪到 `[0, 1]`，公式为：

```text
C = clamp(0, 1,
    0.35 × S + 0.25 × P + 0.20 × E + 0.10 × F + 0.10 × V)

S（来源可靠性）：0 个有效 source_refs -> 0；1 个 -> 0.6；至少 2 个 -> 1
P（发布状态）：PUBLISHED -> 1，否则 -> 0
E（环境匹配）：未声明环境限制或当前环境在列表中 -> 1，否则 -> 0
F（新鲜度）：age_days ≤ 183 时为 1；否则 max(0, 1 - (age_days - 183) / 365)
V（证据完整度）：source_refs、locator、verification_log、experiment_id 四项存在比例
```

本地文本路径中的 `EvidenceChunk` 是运行时证据对象，不写入 SQLite。它的 `chunk_id` 使用 `sha256("<相对路径>:<行号>")`，并加上 `python-grep:` 前缀；`content` 是命中的原始文本行；`vector_score` 与 `rerank_score` 在 B1 阶段未设置，留待 C1 的向量和重排能力使用。`GrepRetriever` 默认最多返回 20 条结果、扫描 200 个文件、读取每个文件最多 1,000,000 字节；读取上限加一个探测字节，以拒绝超限或读取期间增长的文件。
### 检索方式讲解
研究记忆检索
采用“结构化过滤 + 关键词集合匹配”的方式：
从 SQLite 的 research_memories 表读取数据。
要求五维 ScopeKey 完全匹配。
只召回 PUBLISHED 状态。
再过滤实验环境适用性，并处理 conflict_key 冲突。
使用正则 \w+ 分词并执行 casefold()。
查询词只要与标题或正文存在 token 交集，就生成候选。
排序顺序为：标题命中词数量
正文命中词数量
记忆原始 confidence
memory_id 稳定排序
----------------------------------------------------------
也就是说，研究记忆通道本身不是向量检索，也不是完整 BM25，而是：
SQLite Scope/PUBLISHED 过滤
        ↓
适用性和冲突过滤
        ↓
标题、正文的大小写无关 token 交集
        ↓
标题命中数 → 正文命中数 → confidence 排序
-------------------------------------------------------
example:
查询："router budget"
标题："Router design"
正文："budget budget control"

query_tokens   = {"router", "budget"}
title_tokens   = {"router", "design"}
content_tokens = {"budget", "control"}
-------------------------------------------------------
### B1 验收边界

- 已实现：已发布研究记忆召回、五维隔离、环境适用性、冲突消解、固定置信度公式、可信目录内的安全逐行文本检索。
- 未实现：BM25、向量检索、RRF、全局候选融合、本地 LLM 重排、工具预算/调用轨迹、`/research/...` API 和审批流程。
- B1 定向测试覆盖策略、研究记忆检索、文本证据检索和存储层；Windows 无创建符号链接权限时，两个链接边界测试会因 `WinError 1314` 跳过。


文件树只包含以下已实现范围：

```text
core/research_models.py
core/constants.py
core/config.py
api/schemas.py
memory/research_store.py
tests/test_research_models.py
tests/test_research_store.py
```

## C1：Agentic 多路检索、融合与精排

C1 是 ARR 的“检索决策层”。它不直接执行命令、修改文件或绕过安全约束；它只在当前五维 `ScopeKey` 内选择并调用已注册的只读检索工具，将不同召回路径的候选融合、精排后返回可追溯证据。

与 B1 的差别是：B1 分别提供研究记忆和本地文本的单路检索能力；C1 决定“本次应该查哪些路径”、融合多条路径的结果，并对最终候选进行规则/LLM 精排。

### C1 阶段架构图

```text
                         C1：检索决策、融合与精排

query + ScopeKey
      |
      v
+---------------------------+
| LLMQueryPlanner           |
| 只提议 tool_rounds        |
| 失败或禁用 -> 规则计划    |
+-------------+-------------+
              |
              v
+--------------------------------------------------------------+
| RetrievalRouter                                              |
| 强制校验：已注册 / 只读 / Provider / Scope / 索引新鲜度         |
|           最多 6 轮 / 12 次调用 / 危险能力拒绝                 |
+-------------+-------------+-------------+------------------+
              |             |             |                  |
              v             v             v                  v
    +----------------+ +-----------+ +-----------+ +----------------+
    | ResearchMemory | | grep      | | BM25      |  | vector         |
    | Token 覆盖     | | 本地定位   | | 词法召回   |  | 语义近邻       |
    |  历史科研记忆   | | 逐行匹配   | |tdf*饱和曲线|  | 余弦       |   
    +-------+--------+ +-----+-----+ +-----+-----+ +-------+--------+ 
            |                |             |               |
            +----------------+-------------+---------------+
                                           |
                                           v
                              +--------------------------+
                              | HybridRetriever          |
                              | RRF(k=60) + chunk 去重    |
                              +------------+-------------+
                                           |
                                           v
                              +--------------------------+
                              | RuleReranker             |
                              | 覆盖度 + RRF + 完整度     |
                              +------------+-------------+
                                           |
                                           v
                              +--------------------------+
                              | SiliconFlow LLM 精排      |
                              | 可选；失败回退规则结果    |
                              +------------+-------------+
                                           |
                                           v
                RouterResult(evidence, partial, traces, rejections,
                             degradations)
```

### LLM 提议与 Router 强制校验分别做什么

LLM 只负责提出建议，不能直接调用任何工具。例如它可以返回：

```json
{"tool_rounds":[["bm25","vector"]],"reason":"术语匹配与跨概念语义检索"}
```

这只表示“建议同时使用 BM25 与 vector”。真正执行前，`RetrievalRouter` 会重新检查每一个工具：是否注册、是否只读、Provider 是否允许、索引时间是否有效、工具结果是否仍属于当前 `ScopeKey`，以及调用是否超过 6 轮/12 次预算。任何一项不符合就拒绝，LLM 不能绕过这些程序规则。

当 LLM 未启用、超时、HTTP 失败、返回非 JSON、计划中出现未知工具/重复工具/空轮次或超过预算时，Router 会记录脱敏降级码，并改用确定性的规则计划。规则计划对报错、`traceback`、`.py`、`报错`、`异常` 等查询优先选择 `grep + BM25`；其余场景在可用路径中稳定选择研究记忆、grep、BM25 与 vector。

### 四条召回路径

| 路径 | 文件 | 检索方式 | 典型用途 |
|---|---|---|---|
| ResearchMemory | `retrieval/research_retriever.py` | 已发布记忆的 query token 覆盖 | 查询历史实验结论、工作流、验证案例。标题覆盖数优先于正文覆盖数。 |
| grep | `retrieval/grep_retriever.py` | 可信目录内的逐行大小写无关匹配 | 查报错、函数名、配置项、文件名。保持 B1 的路径、链接、敏感内容和读取上限保护。 |
| BM25 | `retrieval/bm25_index.py`、`retrieval/bm25_retriever.py` | Scope 隔离的倒排索引与非负 BM25 IDF | 查专业术语、错误片段、参数名和精确关键词。 |
| vector | `retrieval/vector_retriever.py` | 上游 embedding + 向量索引近邻 | 查跨语言、跨表述或跨概念的语义相似证据。ARR 不实现麒麟 embedding 模型本身。 |

`ResearchMemory` 不是“大模型错误错题本”的同义词。它是经过来源、环境、生命周期和策略筛选的结构化科研记忆；实验失败或代码修复案例只是其中一种记忆类型。

### 候选融合与精排

不同检索器的原始分数没有统一量纲，例如 BM25 分数不能直接与向量相似度相加。因此 C1 对至少两条实际返回候选的路径使用 RRF：

```text
rrf_score(chunk) = sum(1 / (60 + rank_in_retriever))
```

同一 `chunk_id` 的证据必须具有相同的 Scope、来源、定位和内容；身份冲突会拒绝该候选。只有一路有候选时，C1 使用稳定排序且不伪造 `rrf_score`。

规则精排最多处理 20 条去重候选：

```text
coverage   = |query_tokens intersection evidence_tokens| / |query_tokens|
rule_score = 0.70 * coverage
           + 0.20 * normalized_rrf_score
           + 0.10 * evidence_completeness
```

其中完整度检查 `source_ref`、`locator` 和非空 `content`。结果会写入 `rerank_score` 与 `rerank_reason`。可选 LLM 精排成功时可覆盖最终排序；若返回未知/重复/漏失 ID、非法分数、超时或错误，C1 保留规则精排结果。

### C1 关键字段与结果状态

| 字段 | 作用 |
|---|---|
| `bm25_score` | BM25 通道的词法相关性分数。 |
| `vector_score` | 向量通道的语义相似度；必须匹配当前 embedding 模型和 Scope。 |
| `rrf_score` | 多路候选的倒数名次融合分数。 |
| `rerank_score` | 规则或 LLM 精排后的最终分数。 |
| `rerank_reason` | 规则分量说明或经校验的 LLM 排序理由。 |
| `partial` | 有工具失败、无效结果、预算耗尽或降级时为 `true`；已取得的安全证据仍返回。 |
| `traces` | 脱敏调度轨迹：轮次、工具名、候选数、耗时、接受/拒绝原因；不保存 query 正文或密钥。 |
| `rejections` | 白名单、只读、Provider、Scope、新鲜度和预算等拒绝原因。 |
| `degradations` | LLM 计划/精排、工具、融合和结果校验的固定脱敏降级码。 |

### LLM、Prompt 与密钥安全

LLM 路由和精排共用受限的 OpenAI 兼容客户端。Router Prompt 和精排 Prompt 均有版本号；固定安全指令放在 `system` 消息，查询与候选 JSON 放在 `user` 消息，并明确它们是不可信数据、不能改变系统指令。

当前允许端点只有：

```text
https://api.siliconflow.cn/v1
```

其它主机、非 HTTPS、端口、用户名密码、query、fragment 和 HTTP 重定向都拒绝。密钥只从进程环境变量 `ARR_SILICONFLOW_API_KEY` 读取；不能写入源码、Markdown、Prompt、trace、异常、配置对象的 `repr` 或测试夹具。默认 LLM 关闭，因此离线测试稳定使用规则计划与规则精排。

```text
ARR_LLM_ENABLED=true
ARR_SILICONFLOW_API_KEY=<仅在本机环境变量设置>
ARR_LLM_MODEL=<模型名>
ARR_LLM_TIMEOUT_SECONDS=10
```

### C1 文件结构与职责

| 文件 | 职责 |
|---|---|
| `policy/llm_query_planner.py` | LLM 工具计划协议、标准 chat-completions 响应解析、严格 JSON 校验和规则回退。 |
| `policy/retrieval_router.py` | 唯一工具编排入口；执行准入、预算、轨迹、融合、精排和结果身份校验。 |
| `policy/llm_prompts.py` | 路由/精排的版本化 Prompt、固定系统指令和最小 JSON 消息构造。 |
| `retrieval/bm25_index.py` | Scope 隔离的 BM25 倒排索引、完整性验证和原子 JSON 持久化。 |
| `retrieval/bm25_retriever.py` | 词法检索、索引新鲜度与 EvidenceChunk 恢复。 |
| `embeddings/embedding_service.py` | 上游 embedding Provider 契约；不包含模型实现。 |
| `vector_store/vector_service.py` | 上游 VectorStore 与近邻命中契约。 |
| `retrieval/vector_retriever.py` | embedding/vector 调用适配、模型 ID 与 Scope 校验。 |
| `retrieval/research_retriever.py` | 已发布科研记忆的 query 相关性检索与 EvidenceChunk 转换。 |
| `retrieval/hybrid_retriever.py` | RRF 融合、去重和同 ID 身份一致性验证。 |
| `retrieval/llm_reranker.py` | 规则精排、受限 LLM 客户端、允许端点/凭据来源/重定向防护和回退。 |

### C1 已实现保证与后续边界

- LLM 只能提议检索计划，不能执行或放宽 Router 门禁。
- 所有候选和重排结果都必须属于当前完整五维 Scope；跨 Scope、未知、重复或身份替换结果会拒绝并回退。
- BM25、vector、ResearchMemory、grep 的单路异常不阻断其它健康路径。
- 测试使用 Fake embedding、Fake vector store、Fake HTTP transport 和 Fake LLM；不调用真实模型、网络或外部命令。
- C1 已完成；下一阶段 D1 才实现审批、外部执行回执验证与非执行 API，E1 再实现评测与离线 E2E。

### C1 验收结果

```text
专项测试：180 passed
全量测试：408 passed, 2 skipped
```

两项跳过是 Windows 未授予创建符号链接权限导致的既有 grep 边界测试环境限制，与 C1 无关。

## D1：审批、验证回执与公开边界

D1 将 A/B/C 阶段给出的证据和建议变为“人工可控制、结果可追溯”的流程。它不执行命令、不修改目标工作区，也不启动执行子 Agent；它只冻结审批方案、记录人工决定、接收已经发生的验证回执。

### D1 分层架构图

```text
                       D1：审批、回执与安全边界

检索证据 / LLM 或规则建议 / ResearchCase
                    |
                    v
       +----------------------------------+
       | ApprovalService                  |
       | 冻结 payload，计算 SHA-256        |
       | 生成 24h 审批包与 receipt_token   |
       | 禁止自批、惰性过期                |
       +----------------+-----------------+
                        |
                        v
       +----------------------------------+
       | ResearchStore / research_memory.db|
       | approval_packages                |
       | approval_decisions               |
       | verification_runs                |
       | research_audit（脱敏）           |
       +----------------+-----------------+
                        |
             人工批准 / 拒绝
                        |
                        v
      （未来）受控执行子 Agent 
      只能执行已批准的冻结方案
      不属于 D1、D1 不调用它
                        |
                        v 
       +----------------------------------+
       | VerificationService              |
       | 校验 Scope / case / hash / token |
       | 拦截敏感回执                      |
       | receipt_id -> 内部 event_key      |
       | 事务内过期校验与并发幂等          |
       +----------------+-----------------+
                        |
                        v
              VerificationRun + 审计记录

公开 HTTP 边界：api/server.py 未注册 research router
公开 /research/... 请求 -> 404
真实 principal + scope 授权接入后才可重新评审开放
```

### D1 在完整流程中的作用

D1 不是新的检索通道，也不是命令执行器。C1 负责在当前五维 `ScopeKey` 内调用 ResearchMemory token 覆盖、grep、BM25 和 vector 等只读通道，融合、精排并返回 `EvidenceChunk`；上层调用方再根据这些证据形成包含建议动作和验证计划的 `ResearchCase`。D1 从已有 `ResearchCase` 开始工作，把可能产生外部副作用的研究建议纳入人工审批和回执验证：

```text
A：定义并存储研究记忆
          |
          v
B/C：检索、融合、精排 EvidenceChunk
          |
          v
上层调用方形成 ResearchCase
          |
          v
D1：冻结方案 -> 人工审批
          |
          v
ARR 之外的外部执行器执行已批准方案
          |
          v
D1：接收、验证并持久化 VerificationRun
```

#### 1. D1 的输入是 ResearchCase，而不是待执行命令

`RetrievalRouter` 的职责止于返回证据，不会把检索结果直接变成命令，也不会把命令发送给 D1。上层调用方可以引用 C1 返回的 `EvidenceChunk`，形成一个 `ResearchCase`：其中记录研究问题、`evidence_chunk_ids`、`proposed_actions`、验证计划和相关元数据。D1 要求该案例已经存在于同一完整五维 Scope 中，随后才允许创建审批包。

#### 2. ApprovalService 冻结待审批方案

`ApprovalService.create_package` 将案例的建议动作、验证计划和环境约束冻结为 `ApprovalPackage`，计算稳定的 SHA-256 `payload_hash`，生成 `receipt_token`，并设置默认 24 小时有效期。后续提交的执行回执必须继续引用相同的案例、哈希和令牌；如果审批后有人替换建议内容，哈希校验会阻止该回执进入持久化。

审批状态由程序约束，只允许合法转换，并禁止申请人审批自己的申请：

```text
PENDING
   +-- APPROVED
   +-- REJECTED
   `-- EXPIRED
```

因此，D1 在执行前承担的是“允许执行什么”的安全门，而不是“如何执行”的执行引擎。

#### 3. 真正执行发生在 ARR 和 D1 之外

审批通过只表示冻结方案获得人工授权。D1 不调用 Shell、网络、LLM 或执行子 Agent，不修改代码、数据或目标工作区。未来的外部执行器只能读取已经批准的冻结方案并在 ARR 之外执行；当前 V1 尚未提供真实外部执行器。

#### 4. VerificationService 接收已经发生的执行回执

外部执行结束后，`VerificationService.record_receipt` 接收 `VerificationRun`，检查审批包已经批准且未过期，并核对完整 Scope、`case_memory_id`、`payload_hash`、`receipt_token`、环境快照、验证摘要和证据引用。回执中的 token、API key、Bearer 凭据等敏感内容会在写入前被拒绝；`receipt_id` 派生的内部 `event_key` 用于保证并发和重复提交下的幂等性。

#### 5. D1 不自动添加或发布 ResearchMemory

D1 当前持久化的是 `ApprovalPackage`、人工审批决定、`VerificationRun` 和对应审计事件。即使验证状态为成功，也不会自动创建普通 `ResearchMemory`，不会将候选记忆改为 `PUBLISHED`，也不会把研究案例自动变成下一轮可检索知识。若未来需要沉淀验证成功的经验，仍需单独设计“验证结果 -> 候选研究记忆 -> 人工审核 -> 发布”的生命周期流程。

可以用三个问题区分 C1、D1 和外部执行器：

```text
C1：应该参考哪些证据？
D1：基于证据提出的行动是否获批，执行回执是否可信？
外部执行器：如何真正执行已批准的行动？
```

### D1 文件结构与作用

| 路径 | 作用 | 在流程中的位置 |
| --- | --- | --- |
| `core/research_models.py` | 定义审批包、验证回执、`receipt_token`、`receipt_id` 与内部事件键派生。 | 冻结审批单和标识回执。 |
| `core/constants.py` | 定义风险等级、审批决定、审批状态和验证状态枚举。 | 防止状态值随意写入。 |
| `memory/research_store.py` | 创建 D1 表、五维 Scope 隔离、原子最终决定、原子回执写入和审计脱敏。 | 并发一致性的唯一裁决点。 |
| `policy/approval_service.py` | 创建审批包、计算稳定哈希、默认 24 小时过期、禁止自批和重复决定。 | 人工审批之前的安全门。 |
| `policy/verification_service.py` | 校验回执字段、敏感信息、案例/哈希/令牌，调用原子 Store 接口。 | 外部执行结束后的入口。 |
| `api/schemas.py` | 定义未来内部路由使用的审批和回执请求/响应契约。 | 参数校验，不承载业务动作。 |
| `api/routes_research.py` | 保留未来认证完成后的内部路由编排。 | 当前不注册到公开应用。 |
| `api/server.py` | 公开 FastAPI 入口。 | 刻意不注册 research router，所以 `/research` 为 404。 | 
| `tests/test_research_store.py` | 覆盖迁移、Scope、过期、原子事务、回执幂等和审计脱敏。 | 验证数据层正确性。 |
| `tests/test_approval_service.py`、`tests/ test_verification_service.py` | 覆盖审批状态机、回执准入、敏感信息和竞争场景。 | 验证服务层安全规则。 | 
| `tests/test_routes_research.py` | 验证内部路由契约、公开 404 与非执行边界。 | 防止未认证时意外开放接口。 |

### D1 已实现内容与限制

- `ApprovalService` 创建带内容哈希和 24 小时有效期的审批包，强制完整五维 `ScopeKey`、申请/审批分离和状态转换。
- `VerificationService` 只接收已批准、未过期审批包对应的外部回执，校验案例、哈希、环境、验证摘要、证据引用和内部事件键幂等。
- 回执递归拒绝 token、API key、Bearer 凭据等敏感内容；拒绝发生在写入前。
- 公开 `/research/...` 路由刻意未注册，所有公开请求保持 404，等待真实 principal 与 scope 授权接入。

### D1 验收结果

```text
D1 相关回归：63 passed
全量回归：477 passed, 2 skipped in 2.13s
```

两项跳过是 Windows 未授予创建符号链接权限导致的既有 `grep` 边界测试（`WinError 1314`），不属于 D1 失败。

## E1：离线评测、Fake 回执 E2E 与受控真实 LLM 基准

E1 是项目的验收层。它不负责新增检索策略、执行审批方案或修改用户工作区；它负责回答“当前检索、审批和回执链路是否可重复验证，真实 LLM 接入是否受控且可回退”。为避免真实模型的网络波动、密钥和费用影响常规测试，E1 将**离线验收**与**显式手工真实 LLM 基准**严格分成两条路径。

### E1 架构图

```text
                              E1：评测与安全验收

      +-------------------- 离线、可重复、零网络 --------------------+
      |                                                               |
research_gold.json --> research_eval.py --> Recall@K / MRR / scope_leak_count
      |                                                               |
      +--> ResearchCase --> 审批 --> Fake 回执 --> 验证记录与脱敏审计
      |
      `--> 公开 FastAPI 的 /research/* 保持 404
                 （未接入真实 principal 认证和 Scope 授权）

      +---------------- 显式手工、受预算约束的真实模型 --------------+
      |                                                               |
llm_live_eval.json --> run_live_llm_eval.py --> 最多 20 次共享调用预算
      |                                      |
      |                                      +--> LLMQueryPlanner
      |                                      +--> rule / llm / hybrid 重排
      |                                      `--> 脱敏 JSON 报告（Git 忽略）
      |
      `--> 不进入 pytest/CI；不执行审批计划；不修改工作区

共同硬门禁：五维 Scope、Router 白名单、索引新鲜度、6 轮 / 12 次工具预算。
LLM 只能提出规划或排序建议，Router 和重排器仍负责校验、回退和拒绝。
```

### E1 文件结构与位置

| 层次 | 文件 | 作用 |
| --- | --- | --- |
| 离线金标 | `data/gold/research_gold.json` | 存放脱敏、版本化的 Scope、候选 ID 与相关证据 ID；加载时拒绝重复 ID、缺失 Scope 和无效相关 ID。 |
| 指标与 E2E | `evaluation/research_eval.py` | 计算 `Recall@K`、`MRR` 和 `scope_leak_count`；用临时 SQLite 编排“案例 → 审批 → Fake 回执 → 审计”链路，不调用模型或执行器。 |
| 离线测试 | `tests/test_research_eval.py` | 验证金标解析、指标边界、Fake 回执的非执行性、ResearchMemory 不会自动发布，以及公开 `/research/*` 为 404。 |
| 真实 LLM 基准数据 | `data/gold/llm_live_eval.json` | 受控的脱敏合成场景集，覆盖单工具、多工具、未知工具、注入、Scope 边界、预算和不同相关证据位置。 |
| 真实评测核心 | `evaluation/live_llm_eval.py` | 严格校验基准，统一管理 Planner 与 Reranker 共用的 20 次调用预算，统计回退、成功条件与 Scope 泄露。自身不读取环境变量、不发 HTTP。 |
| 手工入口 | `scripts/run_live_llm_eval.py` | 仅由操作者显式运行时读取 LLM 配置；只允许白名单数据集，拒绝绝对路径、`..`、符号链接和不安全报告路径。 |
| Prompt | `policy/llm_prompts.py` | 保存带版本号的 `arr-router-v1/v2`、`arr-rerank-v1/v2`。v2 强制单个 JSON、无 Markdown、候选完整且按输入顺序返回、理由简短。 |
| 规划适配 | `policy/llm_query_planner.py` | 将模型的 `tool_rounds` 建议转换为结构化计划；只接受已注册工具，并限制 6 轮、12 次工具调用。 |
| 重排适配 | `retrieval/llm_reranker.py` | 提供规则重排和可选 LLM 精排；支持 OpenAI 兼容的 `choices[0].message.content` JSON，异常时保留规则排序。 |
| 真实路径测试 | `tests/test_live_llm_eval.py` | 使用 Fake client 验证预算、三种模式、报告脱敏、CLI 白名单、符号链接拒绝和并发不覆盖；绝不产生真实 HTTP 调用。 |
| Prompt/协议测试 | `tests/test_llm_prompts.py`、`tests/test_llm_query_planner.py`、`tests/test_llm_reranker.py` | 分别验证 Prompt 安全约束、规划 JSON 协议和 OpenAI 兼容精排响应解析。 |
| 报告隔离 | `.gitignore` 中的 `data/eval_reports/` | 防止真实基准报告进入 Git。报告仅含 case ID、Prompt 版本、调用计数、降级码和 Scope 指标，不含 query、证据正文、模型响应或密钥。 |

### 三种重排模式

| 模式 | 是否调用真实 LLM | 失败后的结果 | 适用场景 |
| --- | --- | --- | --- |
| `rule` | 否 | 始终使用本地规则排序 | 离线基线、调试、零网络验证。 |
| `llm` | 是，仅限显式手工运行 | 超时、非法 JSON 或不可信响应时回退到规则排序 | 单独观察 LLM 精排效果。 |
| `hybrid` | 是，仅限显式手工运行 | 先获得规则结果；LLM 不可用时保留该结果 | 当前真实基准推荐模式。 |

这里的“使用 LLM”不等于 LLM 能自行执行工具或越过 Scope。即使是 `hybrid` 模式，LLM 超时或返回不合规内容时，系统也会返回已有的规则排序，并以 `llm_timeout`、`llm_invalid_response` 等脱敏码记录原因。

### 真实 Prompt 迭代结果如何阅读

真实基准只使用 10 个合成场景，目的是比较 Prompt 的格式稳定性和回退行为，不能代表真实用户任务的总体质量。受控测试中，`arr-router-v1` 为 4/10 通过、6 次降级、4 次超时、2 次无效响应、0 次 Scope 泄露；调整输出协议后的 `arr-router-v2` 为 7/10 通过、3 次降级、2 次超时、0 次无效响应、0 次 Scope 泄露。

```text
arr-router-v1  : 4 / 10 通过；6 次降级；4 timeout；2 个无效响应；0 Scope 泄露
                      |
                      |  仅收紧 Prompt 的输出格式与候选约束
                      v
arr-router-v2  : 7 / 10 通过；3 次降级；2 timeout；0 个无效响应；0 Scope 泄露
```

该结果说明 v2 改善了受控合成基准上的输出格式稳定性，且没有发现 Scope 泄露；但仍有 2 次超时，不能据此宣称真实模型调用已经稳定。后续应保持同一脱敏基准，持续观察超时、模型版本和网络条件，再由人工设定质量阈值。

### E1 边界总结

- E1 只记录和评测检索、审批、回执行为；不执行审批方案、不修改目标工作区，也不自动发布 `ResearchMemory`。
- 真实 LLM 只在用户明确运行手工脚本时启用；pytest、CI 和公开 FastAPI 始终是无真实模型、无密钥、无网络的路径。
- `/research/*` 仍为 404。未来必须先接入真实 principal 认证和逐请求 Scope 授权，不能把内部 Header 当作信任根。
- RAGAS、线上质量阈值、真实外部执行器和公开 research API 均不属于 E1 当前交付。

## F1：真实科研数据验证与真实检索后端接入（下一阶段）

F1 的目标不是继续扩展 ARR 的执行或公开 API 边界，而是回答一个更基础的问题：当前已经通过合成数据和 Fake 依赖验证的检索架构，在真实科研语料、真实查询和人工相关性标注上是否仍然有效。该阶段由多个角色并行协作，但所有产物必须遵守同一数据版本、完整五维 `ScopeKey`、统一切分规则和统一评测协议。

### 当前仍未真实落地或仍依赖 Fake 的部分

| 能力 | 当前状态 | F1 处理方式 |
| --- | --- | --- |
| Embedding Provider | `embeddings/embedding_service.py` 只定义 `EmbeddingProvider` 和 `EmbeddingResult` 契约，没有具体模型适配器。 | 接入一个可复现的真实本地 embedding 模型，并记录模型 ID、版本、维度和归一化方式。 |
| VectorStore | `vector_store/vector_service.py` 只定义查询协议；`vector_store/mock_vector_store.py` 是测试占位。 | 实现本地 FAISS 或等价向量索引后端，强制 Scope 和 embedding model ID 一致性。 |
| Vector 真实语料索引 | `VectorRetriever` 已实现，但常规测试只连接 Fake embedding 和 Fake vector store。 | 对与 BM25 相同的真实 `EvidenceChunk` 集合构建向量索引。 |
| 检索金标 | `research_gold.json` 是脱敏模拟样本，不能代表真实科研查询分布。 | 建立版本化、脱敏、人工标注的真实科研检索金标。 |
| LLM 基准数据 | `llm_live_eval.json` 使用 10 个 synthetic 场景，主要验证协议和回退。 | 在固定真实候选集上评估 Planner 和 Reranker，不允许模型改变 Scope 或候选身份。 |
| LLM 自动测试 | pytest 使用 Fake HTTP transport 和 Fake client。 | 保留 Fake 自动回归；真实 LLM 仍只通过显式手工脚本运行并输出脱敏报告。 |
| D1 执行结果 | E1 使用 Fake 回执，没有真实外部执行器。 | 不属于 F1；真实检索验证不得被执行器工作阻塞。 |
| 公开 research API | `/research/*` 保持 404，没有真实 principal 和 Scope 授权。 | 不属于 F1；继续保持关闭。 |
| 验证结果转研究记忆 | 成功 `VerificationRun` 不会自动生成或发布 `ResearchMemory`。 | 不属于 F1；后续单独设计候选生成和人工发布流程。 |

### F1 团队协作与依赖关系

```text
数据负责人：真实语料 + 查询 + 人工相关性标注
             |
             +--------------------+
             |                    |
             v                    v
向量检索负责人：真实 embedding    检索集成负责人：真实 grep/BM25/ResearchMemory 基线
             |                    |
             +----------+---------+
                        |
                        v
评测负责人：单路对照 + 多路 RRF + Scope/延迟指标
                        |
                        v
LLM 评测负责人：固定候选集上的 Planner/Reranker 对照
                        |
                        v
安全与验收负责人：数据许可、脱敏、Scope、复现和发布门禁
```

上游产物未冻结时，下游不得自行构造另一套语料、查询 ID、切分规则或相关性标签。任何语料、索引或金标变化都必须更新数据版本，并重新运行所有基线，避免不同角色提交的指标不可比较。

### Task F1-1：建立真实科研语料与人工金标

**负责人角色：数据负责人**

**协作输入：** 研究笔记、论文摘要或正文片段、实验日志、代码与 README、数据集说明、历史问题和已验证结论。

- [ ] 对原始材料进行许可、隐私和敏感信息审查，只保留允许用于本地评测的脱敏内容。
- [ ] 将所有材料转换为稳定的 `EvidenceChunk`，为每个片段分配不可复用的 `chunk_id`、`source_ref` 和 `locator`。
- [ ] 为每条材料写入完整五维 `ScopeKey`；必须包含同主题但不同 team、project、repository、branch 或 experiment environment 的干扰项。
- [ ] 固定切分策略并记录版本，包括最大长度、重叠、标题拼接和代码块处理规则。
- [ ] 编写真实科研查询，并由人工标注 `relevant_ids`；标注者不能根据某个检索器的返回结果反向定义答案。
- [ ] 加入无答案查询、词面不一致但语义相关查询、精确术语查询、代码错误查询和跨 Scope 干扰查询。
- [ ] 生成不含正文和敏感内容的金标校验摘要，包括数据版本、查询数量、候选数量、Scope 数量和标签分布。

**最小验收：** 初始试运行不少于 30 条查询；正式对比建议不少于 100 条查询。金标加载必须拒绝重复 ID、不完整 Scope、相关 ID 不在候选集和跨 Scope 相关项。

### Task F1-2：实现真实 Embedding Provider 和本地 VectorStore

**负责人角色：向量检索负责人**

**依赖：** F1-1 的切分规则和 `EvidenceChunk` schema 已冻结。

- [ ] 实现本地 `EmbeddingProvider` 适配器，首个基线使用固定版本的 sentence-transformers 模型或团队批准的等价本地模型。
- [ ] 查询和文档使用同一模型、同一归一化方式和同一维度；返回真实且稳定的 `model_id`。
- [ ] 实现本地 FAISS 或等价 `VectorStore`，索引记录必须绑定数据版本、完整 Scope、模型 ID、构建时间和 chunk 身份信息。
- [ ] 索引构建只接受 F1-1 产出的同一批 `EvidenceChunk`，禁止为 vector 单独修改正文或切分方式。
- [ ] 查询时先按完整 Scope 隔离，再执行 Top-K；返回结果必须通过 `VectorRetriever` 的 Scope、分数和 model ID 复核。
- [ ] 对空查询、维度不匹配、模型不匹配、非有限向量、损坏索引、未来时间和跨 Scope 命中编写回归测试。
- [ ] 提供可重复的索引构建命令和只包含计数、版本、耗时的脱敏构建报告。

**最小验收：** 同一数据版本重复构建得到相同 chunk 身份集合；已知语义查询能够召回人工标注证据；跨 Scope 返回数必须为 0。

### Task F1-3：完成真实三路基线与四路 Router 集成

**负责人角色：检索集成负责人**

**依赖：** F1-1 可先启动三路基线；F1-2 完成后再加入 vector。

- [ ] 使用真实语料分别构建 ResearchMemory、grep 和 BM25 输入，验证三条现有通道无需 Fake 依赖即可独立运行。
- [ ] 确保 ResearchMemory token 覆盖、grep、BM25 和 vector 使用相同查询文本、Scope 和候选身份定义。
- [ ] 记录每条通道的索引版本、新鲜度、Top-K、耗时、异常和降级码。
- [ ] 将真实 vector 后端注册到 `RetrievalRouter`，保留现有白名单、只读、Provider、新鲜度、6 轮和 12 次调用预算。
- [ ] 验证任何单路失败时其他健康通道仍能返回结果，且 RRF 不接受同一 chunk ID 的内容或来源身份替换。
- [ ] 固化以下运行模式：`research-memory`、`grep`、`bm25`、三路 RRF、`vector`、`bm25+vector`、四路 RRF。

**最小验收：** 所有模式都能对同一金标独立运行；结果包含可追溯 `source_ref` 和 `locator`；任何模式的 `scope_leak_count` 必须为 0。

### Task F1-4：执行真实检索效果、消融和性能评测

**负责人角色：评测负责人**

**依赖：** F1-1 金标冻结，F1-3 提供统一运行模式。

- [ ] 对每种单路和组合模式计算 `Recall@1`、`Recall@5`、`Recall@10`、MRR 和 `scope_leak_count`。
- [ ] 增加无答案查询的误召回率，并单独报告跨 Scope 干扰集结果。
- [ ] 记录索引构建耗时、单查询 P50/P95 延迟、索引大小和失败/降级次数。
- [ ] 执行消融：去掉 ResearchMemory、grep、BM25、vector 或 LLM 精排，观察指标变化。
- [ ] 保留逐查询结果用于人工错误分析，但对外报告只引用脱敏 case ID、排名和错误分类。
- [ ] 对低召回案例按“分词、切分、词面差异、Scope、索引、融合、标注争议”分类，不能只汇报平均分。
- [ ] 使用固定随机种子、数据版本和配置生成可复现报告；数据或模型变化后必须重新跑全部基线。

**最小验收：** 报告能够回答每条通道的独立贡献、RRF 是否提升召回、vector 是否补充词法检索，以及性能是否满足团队设定的离线预算。F1 不预设虚假的质量阈值，首轮真实基线完成后再由人工确定门槛。

### Task F1-5：在固定真实候选集上评估 Planner 与 Reranker

**负责人角色：LLM 评测负责人**

**依赖：** F1-4 已形成不使用真实 LLM 的规则基线。

- [ ] 首先运行 `rule` 模式，冻结每条查询进入精排的候选集合和输入顺序。
- [ ] 在相同候选集上分别运行真实 LLM Planner、LLM Reranker 和 `hybrid` 模式，禁止模型补造候选。
- [ ] 保持每轮最多 20 次真实调用的共享预算，并记录模型 ID、Prompt 版本、超时、非法响应和规则回退次数。
- [ ] 比较规则与 LLM 的 Recall@K、MRR、首个相关结果排名、调用成本和延迟。
- [ ] 加入真实但脱敏的提示注入、未知工具、空计划和跨 Scope 诱导场景，确认 Router 仍拒绝越权计划。
- [ ] pytest 和 CI 继续使用 Fake client；真实 LLM 只允许显式手工运行，报告不得保存 query、证据正文、原始模型响应或凭据。

**最小验收：** 真实 LLM 路径的 `scope_leak_count` 为 0；所有非法、超时或不可信响应均回退到已冻结的规则结果；报告能够判断 LLM 是否带来稳定且值得成本的提升。

### Task F1-6：执行跨角色安全审查与阶段验收

**负责人角色：安全与验收负责人**

**依赖：** F1-1 至 F1-5 的目标产物均已提交。

- [ ] 审查数据来源、许可、隐私和脱敏记录，确认真实语料及评测报告可以在团队约定范围内使用。
- [ ] 验证所有通道、索引、金标和报告使用相同的数据版本、chunk 身份和完整 Scope。
- [ ] 复查索引路径、符号链接、敏感文件、凭据来源和日志输出边界。
- [ ] 从空环境重新执行数据校验、索引构建、三路基线、四路对比和报告生成命令。
- [ ] 运行 ARR 专项测试和全量回归，记录通过、失败、跳过项及其原因；不得仅复制历史测试数字。
- [ ] 抽样核对查询、人工标签和 Top-K 结果，区分检索错误与标注争议。
- [ ] 将最终数据版本、模型版本、配置摘要、指标、已知限制和后续决定写入验收记录。

**最小验收：** 另一位未参与实现的成员能够根据文档在干净环境复现核心指标；没有跨 Scope 命中、敏感数据泄漏或无法解释的身份替换。

### F1 阶段顺序与并行规则

```text
阶段 1：真实三路基线
F1-1 数据与金标 ──> F1-3 ResearchMemory / grep / BM25 ──> F1-4 基线评测

阶段 2：真实 vector
F1-1 冻结切分 ──> F1-2 embedding + VectorStore ──> F1-3 四路集成 ──> F1-4 消融

阶段 3：真实 LLM
规则基线冻结 ──> F1-5 Planner / Reranker 对照 ──> F1-6 安全与复现验收
```

- 数据负责人完成最小金标后，检索集成负责人可立即开始三路基线；不需要等待 vector。
- 向量检索负责人可与三路基线并行，但必须复用已冻结的切分规则和 chunk ID。
- 评测负责人先验收三路结果，再加入 vector，避免向量后端延期阻塞真实数据验证。
- LLM 评测必须等待规则和检索基线冻结，否则无法判断变化来自检索还是模型。
- 安全与验收负责人从 F1-1 起持续审查，最终验收时不得由单个实现角色自证全部结果。

### F1 明确不包含的任务

- 不实现真实外部执行器，不运行审批方案，不修改目标工作区。
- 不开放 `/research/*`，不以内部 Header 代替真实 principal 认证与逐请求 Scope 授权。
- 不自动把 `VerificationRun` 或 `ResearchCase` 发布为 `ResearchMemory`。
- 不增加远程 `WebSearchProvider`、跨项目检索、跨课题组共享或线上自动学习。
- 不在首轮真实基线前引入 RAGAS 或用 LLM 自动生成相关性标签替代人工金标。

这些能力应在 F1 证明检索质量、Scope 安全和可复现性之后分别立项，不能与真实数据验证混在同一个验收门槛中。

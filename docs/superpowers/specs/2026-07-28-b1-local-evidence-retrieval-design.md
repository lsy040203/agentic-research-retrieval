# B1 本地证据与研究记忆检索设计

## 目标

在不联网、不启动外部命令、不写入目标工作区的前提下，为 ARR 提供五维作用域内的已发布研究记忆检索和受限本地文本证据检索。

## 范围

### 已实现目标

- 从 `ResearchStore` 检索完整 `ScopeKey` 一致且状态为 `PUBLISHED` 的 `ResearchMemory`。
- 使用纯 Python 文件遍历、文本读取和正则匹配实现本地文本检索，不调用 `rg.exe`、shell 或任何子进程。
- 以 `EvidenceChunk` 返回文件来源、行号、片段、来源引用和定位信息。
- 以确定性策略过滤生命周期不可用、环境不适用和冲突候选。

### 非目标

- 不实现 Agentic Router、RRF、向量检索、LLM 重排、审批服务、HTTP 路由或评测。
- 不执行项目命令、安装依赖、联网、写入目标工作区或调用外部进程。
- 不默认跨团队、项目、仓库、分支或实验环境检索。

## 组件设计

### `retrieval/research_retriever.py`

输入为 `ScopeKey` 和 `ResearchStore`，调用 `list_published(scope)` 取得候选记忆，再调用 `ResearchPolicy` 过滤和排序。输出为可追溯的 `ResearchMemory` 列表；不直接访问 SQLite 内部表。

### `retrieval/grep_retriever.py`

输入为受信任的检索根目录、相对路径范围、查询文本和结果上限。实现必须：

1. 使用 `pathlib` 遍历，使用 Python 文本匹配或 `re` 匹配；禁止 `subprocess`、shell 和外部命令。
2. 将请求路径解析后限制在允许根目录内；拒绝绝对路径和任何解析后越出根目录的路径。
3. 跳过二进制文件、超出大小上限的文件、敏感文件名和命中敏感内容模式的文本。
4. 限制扫描文件数、单文件读取大小和返回 EvidenceChunk 数。
5. 返回 `EvidenceChunk`，其中 `source_ref` 是受限根目录内的相对路径，`locator` 是行号或行范围，`metadata` 记录检索来源和查询词。

### `policy/research_policy.py`

输入为当前 `ScopeKey` 和候选 `ResearchMemory`。策略不产生副作用：

- 仅允许 `PUBLISHED` 状态；`REVOKED`、`CONFLICT`、`EXPIRED` 或其他非发布状态均不可用。
- `applicability` 与当前 `experiment_environment` 不兼容时过滤。
- 同一冲突主题的多个候选按确定性顺序选择：更高置信度优先，其后 `updated_at` 更新者优先，最后 `memory_id` 字典序作为稳定决胜条件。

## 可解释置信度

`ResearchMemory.confidence` 是 0 至 1 的已有字段。B1 不修改或重新训练该原始分数；只计算用于排序的可解释“检索置信度”：

```text
retrieval_confidence =
  0.35 * source_reliability
  + 0.25 * verification_status
  + 0.20 * environment_match
  + 0.10 * freshness
  + 0.10 * evidence_completeness
```

每个因子归一至 0 至 1：

- `source_reliability`：有来源引用为基础分；具有人类确认、论文原文或成功实验记录标记时提高。
- `verification_status`：`PUBLISHED=1.0`、`VERIFIED=0.8`、`CANDIDATE=0.4`，不可用状态为 0；B1 实际只保留 `PUBLISHED`。
- `environment_match`：当前实验环境精确匹配为 1；明确兼容的环境可为 0.5；不兼容直接过滤。
- `freshness`：以 UTC `updated_at` 计算，六个月内为 1，超过一年按确定性规则衰减。
- `evidence_completeness`：来源、定位信息、验证日志或关联实验标识越完整，得分越高。

分数仅用于过滤和排序，不能触发自动发布、审批或执行。

## 测试与验收

- ScopeKey 任一维度不一致时，研究记忆检索不得返回候选。
- 非发布/已撤销记忆、环境不匹配记忆不得返回。
- 冲突候选按置信度、更新时间和 ID 稳定排序。
- 文件检索拒绝绝对路径、路径逃逸、二进制、敏感内容和超限扫描。
- 测试通过 Fake/临时目录验证不会启动子进程、不会写文件、不会联网。
- 全部测试使用 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe`。

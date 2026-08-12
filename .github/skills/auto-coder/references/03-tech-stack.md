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
| `research_audit` | 保存、撤销等审计记录。 |

`EvidenceChunk` 当前无持久化表。`ResearchCase` 可以以 `kind=research_case` 保存其继承的 `ResearchMemory` 通用字段，但案例专用字段 `evidence_chunk_ids`、`proposed_actions` 和 `metadata` 尚未持久化；审批包与验证回执的持久化/API 也属于后续阶段，不能据此宣称 B1-E1 已完成。

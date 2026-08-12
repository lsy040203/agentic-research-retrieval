# D1：审批、验证回执与非执行 API 设计

**状态：已确认，待编写实施计划**  
**日期：2026-07-29**  
**对应阶段：D1（依赖 A2、C1）**

## 1. 目标、范围与非目标

D1 为 ARR 中可能产生外部副作用的研究案例建立可追溯的人工审批链。它把待执行方案冻结为审批包，由人工批准或拒绝；实际执行始终由 D1 之外的受控执行子 Agent 完成。执行结束后，执行子 Agent 向 D1 提交带证据的验证回执，D1 校验其与原审批包一致后持久化结果。

本阶段实现：

- 在现有 `research_memory.db` 中持久化审批包、审批决定、验证回执和审计事件；
- 审批状态流转、24 小时失效、申请人与审批人分离；
- 验证回执的 Scope、冻结内容哈希、审批状态和幂等校验；
- 保留非执行型 HTTP 路由模块与测试，但在认证/授权边界接入前不注册到公开 FastAPI 应用；
- 全部离线测试，不调用真实 LLM、网络、外部执行器或终端命令。

本阶段不实现：

- 实现执行子 Agent，或执行 shell、代码、测试、修复及任何写入目标工作区的动作；
- 让 LLM 绕过人工审批自主执行；
- 用户登录、OAuth、完整 RBAC 或外部执行器身份认证；
- 自动将成功回执发布为 `ResearchMemory`；该沉淀流程留给后续阶段的质量门与生命周期治理。

## 2. 方案选择

采用“与研究记忆共用 `research_memory.db`”的持久化方案。

```text
方案                         优点                         结论
research_memory.db 新增表    可关联案例、迁移最少、可审计   采用
仅内存保存                   代码少，但重启后状态丢失       不采用
独立 approval.db             物理隔离，但增加跨库复杂度     不采用
```

共享数据库不表示审批数据与普通研究记忆混淆：审批包和回执使用独立表、独立主键和完整五维 `ScopeKey`。`research_memories` 既有表不改变语义。

## 3. 总体架构与数据流

```text
ResearchCase / 已检索证据 / 建议动作
                |
                v
       ApprovalService.create_package()
                |
                | 冻结 payload + SHA-256 内容哈希 + 24h expires_at
                v
             research_memory.db
       +---------------------------+
       | approval_packages         |
       | approval_decisions        |
       | verification_runs         |
       | research_audit（审计）    |
       +---------------------------+
                |
                v
      人工 approve / reject（申请人与审批人不同）
                |
                v
受控执行子 Agent 执行已批准方案（D1 不实现、不调用）
                |
                v
 VerificationService.record_receipt()
                |
                | 校验 Scope、内容哈希、approved、未过期、幂等键
                v
       VerificationRun + 审计事件 + 非执行 API 响应
```

“验证回执”是执行子 Agent 完成外部操作后的结果报告，而不是 D1 执行命令的结果。其至少包含审批包 ID、执行状态、环境快照、验证命令/断言摘要、结果摘要和证据引用；日志仅保存已脱敏摘要，不保存敏感原文。执行子 Agent 未来只能使用审批包中冻结的动作、Scope 与环境约束；不得自行扩大修改范围或追加命令。

## 4. 领域与持久化设计

### 4.1 领域对象扩展

保留现有 `ApprovalPackage` 与 `VerificationRun` 作为纯领域对象，并补足实现所需字段：

| 对象 | 关键字段 | 含义 |
| --- | --- | --- |
| `ApprovalPackage` | `package_id`、`case_memory_id`、`scope`、`requested_by`、`payload`、`payload_hash`、`risk_level`、`status`、`created_at`、`expires_at` | 不可变的待审执行方案。`payload` 包含建议动作、验证计划和环境约束。 |
| 审批决定 | `package_id`、`decision`、`approver_id`、`reason`、`decided_at` | 人工批准或拒绝记录；同一审批包只允许一个最终决定。 |
| `VerificationRun` | `run_id`、`package_id`、`case_memory_id`、`scope`、`receipt`、`status`、`created_at`、`verified_at` | 受控执行子 Agent 返回的验证报告。 |

`risk_level` 采用 `low`、`medium`、`high`。所有外部副作用方案均需要人工审批；高风险案例在满足中风险验证条件后，仍必须由人工明确批准，D1 不提供自动放行路径。

### 4.2 SQLite 表

| 表 | 关键列与约束 |
| --- | --- |
| `approval_packages` | `package_id` 主键；五维 Scope；`case_memory_id`；`requested_by`；冻结 `payload_json`；`payload_hash`；风险和状态；创建/过期时间。按 Scope、状态和过期时间建索引。 |
| `approval_decisions` | `package_id` 唯一；`decision` 为 `approved` 或 `rejected`；`approver_id`；理由与决定时间。禁止 `approver_id == requested_by`。 |
| `verification_runs` | `run_id` 主键；`package_id`；五维 Scope；`receipt_json`；状态；创建/验证时间；ARR 派生的内部 `event_key` 唯一以保证重试幂等。 |
| `research_audit` | 复用现有审计表，写入 `approval_created`、`approval_approved`、`approval_rejected`、`approval_expired`、`verification_recorded` 等脱敏事件。 |

数据库迁移继续采用 `ResearchStore._initialize()` 的幂等建表/建索引风格。所有时间统一转换成 UTC ISO-8601；JSON 使用稳定序列化（排序键、紧凑分隔符）后计算 SHA-256，防止相同内容因字段顺序不同产生不同哈希。

## 5. 状态机与规则

```text
pending --人工批准--> approved --提交回执--> passed / failed / blocked
   |
   +--人工拒绝--> rejected
   |
   `--超过 expires_at --> expired
```

- 创建时状态固定为 `pending`，默认 `expires_at = created_at + 24h`；
- 每次读取、决定和提交回执前都会惰性检查过期状态，将过期的 `pending` 审批包原子地转为 `expired` 并写审计；
- 只有未过期 `pending` 包可被人工决定；`approved`、`rejected`、`expired` 均不可再次决定；
- 只有未过期 `approved` 包可接收验证回执；
- `requested_by` 与 `approver_id` 必须不同且均为非空标识。D1 只接受调用方传入的轻量身份标识，真实认证/授权由后续接入层负责；
- `VerificationService` 不自行判断实验是否真实成功，而是验证回执的结构、来源关联与状态合法性，并保存外部声明的 `passed`、`failed` 或 `blocked`；
- 回执的 `package_id`、`case_memory_id`、完整 Scope 和 `payload_hash` 必须与审批包一致。任一不一致即拒绝写入；
- 审批通过后，ARR/主 Agent 为该审批包发放一次性、高熵 `receipt_token`；执行子 Agent 只需在回执中提供本地 `receipt_id`（如 `step-1`）。ARR 以 `package_id + receipt_token + receipt_id` 稳定派生内部 `event_key`，并只持久化派生键；执行子 Agent 不需要自行生成全局唯一 ID。
- 同一审批包、令牌和 `receipt_id` 重试返回原有回执，不重复创建记录和审计事件；不同 Scope 之间不会因相同简单 `receipt_id` 发生冲突或泄露使用状态。

## 6. 服务边界与接口

| 文件 | 职责 | 禁止行为 |
| --- | --- | --- |
| `policy/approval_service.py` | 创建、查询、到期、批准、拒绝审批包；调用 SQLite 存储接口。 | 不调用 LLM、命令或执行器。 |
| `policy/verification_service.py` | 校验并记录外部验证回执；提供回执查询。 | 不运行验证命令，不根据文字伪造通过结果。 |
| `memory/research_store.py` | 新增审批/回执表迁移与作用域隔离读写。 | 不承载 HTTP 或审批业务规则。 |
| `api/schemas.py` | 新增 D1 请求/响应 schema、字段长度和枚举校验。 | 不含服务逻辑。 |
| `api/routes_research.py` | 保留将 HTTP 请求映射到服务的内部路由模块；认证边界完成前不注册到公开应用。 | 不执行外部操作，也不作为公开入口。 |

认证/授权边界完成后可注册的内部 API：

```text
POST /research/approvals
GET  /research/approvals/{package_id}
POST /research/approvals/{package_id}/decision
POST /research/verifications
GET  /research/verifications/{run_id}
```

创建审批由服务端生成 `payload_hash`、24 小时过期时间与回执令牌；普通响应不返回回执令牌。认证/授权边界完成前，公开应用不注册这些路径，所有 `/research/...` 请求返回 404；路由层仅作为未来内部接入的参数映射，不存在“执行”端点。

## 7. 错误处理与安全

| 场景 | 行为 |
| --- | --- |
| Scope、案例 ID 或内容哈希不匹配 | 拒绝请求，不创建回执。 |
| 审批包不存在或不在请求 Scope 内 | 返回不存在，避免泄露其他 Scope 数据。 |
| 申请人自批、空身份标识 | 返回校验错误。 |
| 已决定、已过期或非 `approved` 审批包提交回执 | 返回状态冲突，不执行任何外部动作。 |
| 相同 `event_key` 重试 | 返回已有记录，确保幂等。 |
| 回执包含敏感字段或超长日志 | schema/服务层拒绝或只保留脱敏摘要；不写原始密钥、令牌或完整敏感日志。 |
| SQLite 异常 | 返回受控服务错误，不暴露 SQL、数据库路径或 payload 内容。 |

审计内容只记录动作、主体 ID、对象 ID、状态、时间和必要的脱敏摘要；不得记录 API 密钥、完整命令输出或未清洗的证据正文。

## 8. 测试与验收

测试采用 TDD，使用临时 SQLite 文件和项目既有 FastAPI 测试客户端；不得调用真实网络、LLM、shell 或执行器。

至少覆盖：

1. 创建审批包默认 24 小时有效、UTC 时间和稳定内容哈希；
2. 五维 Scope 隔离：其他 Scope 无法读取、决定或提交回执；
3. 申请人与审批人分离、批准/拒绝的单次终态和审计；
4. 惰性到期：过期审批无法批准或接收回执，并被标为 `expired`；
5. 回执必须匹配审批包、案例 ID、Scope、内容哈希与 `approved` 状态；
6. `passed`、`failed`、`blocked` 回执持久化及 `event_key` 幂等重试；
7. 路由模块为非执行型且公开应用不注册 `/research`：无 subprocess、网络客户端或目标工作区写入；
8. D1 专项测试和全量 `pytest -q` 在指定 Conda Python 环境通过。

验收命令：

```powershell
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q
```

## 9. 实施顺序

1. 先补充/收紧 D1 领域模型和 HTTP schema，并写失败测试；
2. 扩展 `ResearchStore` 的 SQLite 迁移、隔离查询和幂等审计；
3. 实现 `ApprovalService`，验证状态机、过期和职责分离；
4. 实现 `VerificationService`，验证回执的一致性与幂等；
5. 实现非执行 API 与 API 测试；
6. 执行专项及全量回归；通过后才将 D1 标为完成，并同步 `DEV_SPEC.md`、排期、实施计划和中文阶段讲解。

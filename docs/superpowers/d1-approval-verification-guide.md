# D1 审批、验证回执与公开边界指南

## 目标与边界

D1 把 ARR 在 A/B/C 阶段形成的“证据与修改建议”，变成可追溯、可人工控制的行动流程。它不执行代码、不运行测试命令、不修改目标工作区，也不启动执行子 Agent。

它负责保存待执行方案，等待人工决定，并接收外部执行子 Agent 已经完成操作后的验证回执。

```text
检索证据 + LLM/规则建议
          |
          v
    ApprovalPackage（冻结方案）
          |
          v
      人工批准 / 拒绝
          |
          v
（未来）受控执行子 Agent
          |
          v
 VerificationRun（验证回执） + 审计
```

高风险方案仍必须先满足中风险验证条件，并经人工明确批准。D1 没有任何自动批准或自动执行路径。

## 审批包与回执

`ApprovalPackage` 是不可变的审批单。它包含案例 ID、完整五维 `ScopeKey`、申请人、冻结的建议动作、内容哈希、风险等级、创建/过期时间及一次性 `receipt_token`。

- 服务端以稳定 JSON 序列化计算 `payload_hash`，调用方不能伪造。
- 默认有效期为 24 小时。
- 申请人不能批准自己的审批单。
- 到期的 `pending` 审批单会原子转为 `expired`。

`VerificationRun` 是执行后的结果报告。执行子 Agent 只需提交简单本地 `receipt_id`，例如 `step-1`；它不必生成全局唯一 ID。

```text
package_id + receipt_token + receipt_id
                |
                v
      derive_receipt_event_key() -> 内部唯一 event_key
```

因此，不同项目或不同审批包可以都使用 `step-1`，但内部事件键不同；同一审批包的同一回执重试会返回原有记录。

## 原子状态与并发控制

审批决定和回执写入均由 SQLite 事务完成，而不是先在 Python 中检查、稍后再写入。

```text
审批决定事务：
pending + expires_at > now
   -> 写入唯一决定
   -> 状态改为 approved / rejected

回执事务：
approved + expires_at > now + Scope/case/hash/token 一致
   -> INSERT 回执
   -> 同 event_key 冲突时读取已有记录并比较语义
```

这样可避免两个并发请求同时批准、在到期瞬间写入回执，或重复回执因随机 `run_id` 不同而失败。已有回执的相同重试即使审批单之后已过期，也会返回历史记录；这不是新的执行授权。

## 回执安全与审计

回执只允许环境快照、验证摘要、证据引用、断言和有限长度日志摘要等字段。服务会递归检查所有字符串叶节点，拒绝 API key、token、password、secret、private key、Bearer 凭据及其大小写或驼峰变体。

拒绝发生在写入前，因此敏感回执不会进入 `verification_runs`。审计记录只保存动作、主体、对象 ID、状态、时间及必要脱敏摘要，不保存冻结 payload、完整回执、令牌或日志正文。

## 持久化表与文件职责

```text
research_memory.db
  |- research_memories             ResearchCase 等研究记忆
  |- approval_packages             冻结审批方案和 receipt_token
  |- approval_decisions            唯一人工最终决定
  |- verification_runs             已验证回执和内部 event_key
  `- research_audit                脱敏审计事件
```

| 文件 | 作用 |
| --- | --- |
| `core/research_models.py` | 定义审批包、回执、Scope 和内部事件键派生。 |
| `memory/research_store.py` | SQLite 迁移、Scope 隔离、原子审批/回执写入和幂等。 |
| `policy/approval_service.py` | 生成审批包、24 小时过期、人工决定和禁止自批。 |
| `policy/verification_service.py` | 校验回执字段、敏感内容、令牌与证据后记录结果。 |
| `api/routes_research.py` | 未来认证完成后可接入的内部路由编排模块。 |

## 为什么公开 API 返回 404

`api/routes_research.py` 当前没有注册到 `api/server.py`，所以公开 `/research/...` 请求一律为 404。

这是刻意的安全边界：当前项目还没有真实认证 principal 与 Scope 授权系统。仅凭客户端传来的用户 ID 或 HTTP Header 不能证明审批人身份，也不能安全发放 `receipt_token`。认证边界接入后，才可重新评审并注册内部路由；普通审批查询响应也不应返回回执令牌。

## 测试证据与 E1 衔接

2026-07-29 全量离线回归：

```text
477 passed, 2 skipped in 2.13s
```

两项跳过均为 Windows 环境没有创建符号链接权限导致的既有 grep 边界测试，不属于 D1 失败。

E1 将在 D1 的审批、回执和审计基础上补齐离线评测、端到端案例、指标统计与面向使用者的说明；执行子 Agent 与真实认证/授权属于后续独立接入工作。

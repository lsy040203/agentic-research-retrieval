## 实施排期与依赖

| 阶段 | 状态 | 依赖 | 交付与验收 |
| --- | --- | --- | --- |
| P0 | ✅ 已完成 [] | 无 | pytest 基线和 demo fixture 可用。 |
| A1 | ✅ 已完成 [] | P0 | 领域模型、枚举、schema 及其测试通过。 |
| A2 | ✅ 已完成 [] | A1 | 独立 SQLite、关联和审计测试通过。 |
| B1 | ✅ 已完成 [x] | A2 | 本地证据与研究记忆检索；路径边界、生命周期、适用性和冲突测试通过。 |
| C1 | ✅ 已完成 [x] | B1 | Agentic Router、BM25/vector/ResearchMemory 召回、RRF、规则/受限 LLM 精排、预算、轨迹与降级已通过专项和全量回归。 |
| D1 | ✅ 已完成 [x] | A2、C1 | 审批包/决定和验证回执已持久化并由服务与内部路由契约覆盖；公开 `/research/...` 路由刻意未注册，所有公开请求保持 404，待真实 principal 与 scope 授权后再开放。 |
| E1 | ✅ 已完成 [x] | D1 | 离线评测、Fake 回执 E2E、中文用户文档和受控真实 LLM Prompt v1/v2 基准已验收；Task 4 专项 `173 passed`，全量 `564 passed, 2 skipped`。真实调用仅手工触发、每轮最多 20 次，不进入 pytest/CI；v2 仍有 2 个 timeout。 |

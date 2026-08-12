# 科研 ARR V1 排期

> 当前规格：`DEV_SPEC.md`  
> 权威设计：`../docs/superpowers/specs/2026-07-27-agentic-research-retrieval-design.md`

| 阶段 | 目标 | 开始条件 | 完成条件 | 状态 |
|---|---|---|---|---|
| P0 | 测试基线 | 指定 Conda 环境可用 | `python -m pytest -q` 可运行并记录结果 | ✅ 已完成 |
| A | 研究记忆基础 | P0 | 独立表、关联与作用域测试通过 | ✅ 已完成 |  
| B | 研究证据检索 | A | 本地证据、生命周期和冲突测试通过 | 🔄 进行中 [~] | 
| C | Agentic 决策 | B | 白名单、预算、RRF、重排与轨迹测试通过 | ⬜ 未开始 | 
| D | 修复案例治理 | A、C | 审批、回执和非执行 API 测试通过 | ⬜ 未开始 | 
| E | 评测与验收 | D | 专项 E2E 与全量回归通过 | ⬜ 未开始 |

自动实现只能在详细实施计划生成、每项任务补充文件和验收测试后开始。所有 Python 命令使用 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe`。

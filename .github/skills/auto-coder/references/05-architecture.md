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
| `api/routes_research.py` | D1 | 暴露非执行型研究 API；只编排 schema 与服务层。 |
| `evaluation/research_eval.py`、`data/gold/research_gold.json` | E1 | 离线评测、金标数据和 E2E 验收输入。 |

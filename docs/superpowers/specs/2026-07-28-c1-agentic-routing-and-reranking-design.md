# C1：Agentic 路由、混合检索与两级精排设计

**状态：已确认，待编写实施计划**  
**日期：2026-07-28**  
**对应阶段：C1（依赖 B1）**

## 1. 目标、范围与非目标

C1 为 ARR 提供受控的只读检索编排：在五维 `ScopeKey` 隔离、工具白名单、调用预算和可审计轨迹约束下，组合研究记忆、代码/实验记录定位、BM25 与向量检索，返回带来源和适用条件的 `EvidenceChunk`。

V1 范围包括：

- `ResearchMemory` 已发布记忆通道、受限本地 `grep`、纯 Python BM25、上游麒麟 embedding 驱动的向量检索；
- 任意可用的两路或多路候选使用 RRF（`k=60`）融合；
- 版本化 Prompt 驱动的 LLM 路由提议与 LLM 精排；默认关闭，仅允许受限的 SiliconFlow HTTPS 服务；
- Router 的只读准入、Scope/Provider/新鲜度校验、最多 6 轮和 12 次调用预算、非敏感轨迹与确定性回退；
- 全部离线测试，使用 Fake embedding、Fake vector store 和 Fake LLM。

非目标：实现或训练 embedding 模型、下载模型权重、替代队友的麒麟 embedding、联网检索、除 SiliconFlow 明确允许端点外的远程 LLM、执行 shell/代码、写入目标工作区、审批与外部执行。Cross-encoder 不在本阶段实现。

## 2. 总体架构

参考 Modular RAG 的“查询分析—稀疏/稠密召回—RRF—重排”分层，但 ARR 只保留本项目所需的检索决策模块，不引入其 MCP、文档摄取或响应组装层。

```text
查询 + ScopeKey
       |
       v
LLMQueryPlanner / RetrievalRouter
  |- LLM 只提议已注册工具与顺序；失败时规则选路
  |- Router 强制校验后才选择已注册、只读且可用的检索工具
  |- 校验 Scope、Provider、索引新鲜度与轮次/调用预算
       |
       +----------------------+--------------------+-------------------+
       |                      |                    |                   |
       v                      v                    v                   v
ResearchMemory             grep                 BM25                Vector
（已发布、可用记忆）   （本地文件定位）       （词法稀疏）    （麒麟 embedding）
       |                      |                    |                   |
       +----------------------+--------------------+-------------------+
                                      |
                                      v
                         HybridRetriever：RRF(k=60)
                                      |
                                      v
                   RuleReranker：覆盖度 + RRF + 完整性
                                      |
                   +------------------+------------------+
                   |                                     |
                   v                                     v
       LLMReranker 成功                        默认关闭/失败/超时/无效
                   |                                     |
                   +------------------+------------------+
                                      |
                                      v
               EvidenceChunk[] + partial + 脱敏路由轨迹
```

检索路由不固定全开。例如它可以选择 `grep + BM25`、`BM25 + vector`，或 `ResearchMemory + grep + BM25 + vector`。只有实际执行两路及以上且存在候选时才调用 RRF；单路结果走确定性排序，不伪造 RRF 分数。

## 3. 检索通道与接口边界

| 通道/组件 | 职责 | 关键边界 |
| --- | --- | --- |
| `ResearchMemoryTool` | 从独立 SQLite 中取同 Scope、已发布且适用的结构化研究记忆。 | 研究记忆不是“错误错题本”专用；可包括知识、实验配置、工作流和已验证案例。当前 B1 仅做生命周期/适用性筛选，C1 应补足查询相关性召回适配。 |
| `GrepTool` | 受限纯 Python 文件定位，适合文件名、报错和代码标识。 | 不启动 shell/subprocess，不越过路径、链接、敏感内容与读取上限边界。 |
| `BM25Tool` | 对 ARR 本地索引进行稀疏词法召回，适合术语、文件名和精确匹配。 | 采用独立、可测试的 Python 索引与查询接口；不依赖 embedding。借鉴 Modular RAG 的 `BM25Indexer + SparseRetriever` 分层，不复制实现。 |
| `VectorTool` | 以 query 向量从上游向量索引取语义近邻。 | 不实现 embedding；只通过 `EmbeddingProvider` 和 `VectorStore` Protocol/Adapter 对接队友实现的麒麟 embedding 与索引。 |
| `HybridRetriever` | 合并不同通道候选并按 `chunk_id` 去重、RRF 融合。 | 同 ID 的 Scope、来源和定位信息冲突时拒绝该候选；不混合原始分数。 |
| `RuleReranker` | 在最多 20 条去重候选上执行本地确定性精排。 | 不依赖网络或模型。 |
| `LLMQueryPlanner` | 仅提议检索工具组合与顺序。 | 只接收可用工具摘要，严格 JSON 输出；不能执行工具，非法/失败时回退纯规则计划。 |
| `LocalLLMReranker` | OpenAI 兼容 LLM 终排。 | 默认关闭且配置留空；仅允许 SiliconFlow 受限 HTTPS 端点；异常、超时或无效响应稳定回退到规则结果。 |

所有检索工具实现统一的只读契约：输入 `query`、`ScopeKey` 与受控参数，输出 `list[EvidenceChunk]`。每个 `EvidenceChunk` 必须含 `chunk_id`、`scope`、`source_ref` 和可定位信息；向量结果额外在 metadata 中保留 `embedding_model_id` 与索引版本，禁止将不兼容模型的 query 向量用于旧索引。

### 3.1 BM25 索引实现模式

BM25 参考 `D:\agent_study\MODULAR-RAG-MCP-SERVER-main\src\ingestion\storage\bm25_indexer.py` 与 `src\core\query_engine\sparse_retriever.py` 的职责拆分：

```text
EvidenceChunk（已通过 Scope/敏感性边界）
        |
        v
BM25Index：token 词频、文档长度、倒排 posting、IDF、索引元数据
        |
        v
JSON 持久化索引（schema/index_version/built_at/Scope 标识）
        |
        v
BM25Retriever：query tokens -> BM25 排名 -> 恢复完整 EvidenceChunk
```

`BM25Index` 负责建立、加载、查询和原子写入索引；`BM25Retriever` 负责查询参数、Scope/新鲜度检查、EvidenceChunk 恢复与标准化输出。两者均通过依赖注入测试。实现只使用标准库；这是为了离线可复现和当前 Conda 环境无需新增包，并不表示另行设计一种 BM25 算法。

评分采用非负、稳定的 BM25 IDF 变体，避免参考实现中极高频词可能产生负 IDF 的排序歧义：

```text
idf(term) = log(1 + (N - df + 0.5) / (df + 0.5))
score(doc, query) = sum(idf(term) * tf * (k1 + 1)
                        / (tf + k1 * (1 - b + b * doc_length / avg_doc_length)))
```

默认 `k1=1.5`、`b=0.75`，并在索引元数据中保存。索引 schema 必须包含 `index_version`、`built_at`、文档数、平均文档长度、Scope 标识和每个候选的完整恢复信息；查询时拒绝 Scope 不一致、schema 不兼容或新鲜度超限的索引。

## 4. 路由、融合与精排规则

### 4.1 LLM 路由提议与 Router 强制校验

默认请求由 `LLMQueryPlanner` 产生候选 `tool_rounds`。它只可看见已注册工具的名称、能力摘要、查询文本和固定预算，必须输出：

```json
{"tool_rounds":[["bm25", "vector"]],"reason":"术语匹配与跨概念语义检索"}
```

路由 Prompt 必须版本化，并要求模型：仅从提供的工具名选择、最多 6 轮和 12 次调用、不得提出执行/写入/联网搜索、只输出 JSON。Prompt 和 trace 均不得包含 API key 或未经脱敏的候选证据全文。

LLM 只具有“提议”能力，不具有工具调用权。`RetrievalRouter` 在执行前强制验证每一个名称、轮次与调用：白名单、只读属性、Provider、五维 Scope、索引新鲜度、6 轮/12 次预算和危险能力拒绝仍由程序完成。任一项不通过即拒绝该项且不调用工具。LLM 禁用、超时、网络异常、非成功状态、非法 JSON、未知/重复工具名或预算超限时，Router 使用确定性的规则计划继续执行。

### 4.2 路由准入

Router 只调用已注册的本地只读工具。工具必须通过完整 Scope、一致 Provider、本地白名单和索引新鲜度检查；名称或 Provider 表明远程 LLM、WebSearch、shell、命令执行或写入能力时直接拒绝。单请求上限为 6 轮、12 次工具调用；预算耗尽、工具可恢复失败或连续一轮无新增 `chunk_id` 时停止后续调用并返回已得结果，标记 `partial=true`。

每条轨迹记录轮次、工具名、输入摘要、候选数、耗时、接受/拒绝状态和原因；不得记录密钥或敏感证据全文。

### 4.3 RRF 融合

对同一 `chunk_id` 在各实际执行通道中的排名求和：

```text
rrf_score(chunk) = sum(1 / (60 + rank_in_retriever))
```

输入不可修改。相同 `chunk_id` 的重复只计同一路首次出现；融合后的并列按 `source_ref`、`locator`、`chunk_id` 稳定排序。单路候选复用旧对象时移除过时的 `rrf_score`。

### 4.4 本地规则精排

保持已经确认的 token 覆盖思路：以 query token 为分母，使用 Unicode 单词与连续中文片段的纯 Python 提取、`casefold` 和去重，不引入外部分词依赖。

```text
coverage   = |query_tokens ∩ evidence_tokens| / |query_tokens|
rule_score = 0.70 * coverage
           + 0.20 * normalized_rrf_score
           + 0.10 * evidence_completeness
```

完整性取 `source_ref`、`locator`、非空 `content` 三项存在比例。输出必须写入 `rerank_score` 和含 coverage/RRF/完整性明细的 `rerank_reason`。

### 4.5 LLM 精排与受限远程配置

`LLMReranker` 使用 `enabled`、`base_url`、`api_key`、`model` 和 `timeout_seconds` 配置，以及可注入 `LLMClient` 协议。C1 实现 OpenAI 兼容 HTTP 适配器，并在路由和精排中复用同一份 LLM 配置。精排 Prompt 也必须版本化，只允许返回 `chunk_id`、有限 `[0,1]` 分数和非空简短理由。

默认配置固定为 `enabled=false`、`base_url=""`、`api_key=""`、`model=""`。此状态不得创建 HTTP 客户端或发出请求，只返回规则精排结果和 `llm_disabled` 降级原因。启用时，`base_url` 必须精确匹配受配置管理的 SiliconFlow HTTPS 允许端点；包括 loopback 在内的其它任意端点均拒绝。`api_key` 只从进程环境变量读取，绝不写入仓库、测试夹具、Prompt、轨迹、异常或返回理由。

LLM 每次最多处理 20 条去重候选。缺失配置、超时、网络异常、非成功 HTTP 状态、未知/重复 ID、漏项、非有限分数或空理由均回退为规则排序；回退时保留可读降级码并按需要标记 `partial=true`。

## 5. BM25 与向量的可用性/回退

| 场景 | 行为 |
| --- | --- |
| BM25 索引缺失、过期或查询异常 | 跳过 BM25 通道，记录脱敏原因，继续其他通道。 |
| 上游麒麟 embedding 不可用 | 不生成替代 embedding；跳过向量通道，继续其他通道。 |
| query 向量模型 ID 与向量索引不一致 | 拒绝向量通道，记录 `embedding_model_mismatch`。 |
| 向量索引不可用/过期/异常 | 跳过向量通道，继续其他通道。 |
| 任一工具失败 | 不中断其余只读通道；若已有候选，返回部分结果。 |
| 所有通道均不可用或无候选 | 返回空证据与完整脱敏轨迹，不编造答案。 |

## 6. 文件责任与测试验收

| 文件 | 责任 |
| --- | --- |
| `retrieval/bm25_retriever.py`、`retrieval/bm25_index.py` | 纯 Python BM25 索引、Scope 过滤和词法召回。 |
| `embeddings/embedding_service.py`、`embeddings/kylin_embedding.py` | 上游 embedding 的契约及麒麟适配器占位/接入点，不实现模型。 |
| `vector_store/`、`retrieval/vector_retriever.py` | 向量存储契约、Fake 实现与向量召回适配。 |
| `retrieval/research_retriever.py` | 将已发布、适用的研究记忆适配为按 query 参与的证据通道。 |
| `policy/retrieval_router.py` | 工具注册、计划选择、预算、安全准入、轨迹和回退。 |
| `retrieval/hybrid_retriever.py` | RRF 融合。 |
| `policy/llm_query_planner.py` | Prompt 版本、LLM 路由计划协议、严格 JSON 解析与规则回退。 |
| `retrieval/llm_reranker.py` | 规则精排、LLM 协议、OpenAI 兼容受限 HTTP 适配器与回退。 |

验收必须使用：

```powershell
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q
```

至少覆盖：BM25 精确召回、索引新鲜度与 Scope 隔离；Fake embedding/Fake vector store 的模型 ID 校验、Scope 过滤和不可用回退；2 至 4 路任意组合的 RRF；LLM 路由计划的严格 JSON、未知工具/超预算拒绝和规则回退；Router 的 6 轮/12 次预算和脱敏轨迹；规则精排；Fake HTTP transport 的 OpenAI 兼容请求/响应、空配置禁用、允许端点拒绝、超时与无效响应回退；全量离线回归。测试不得调用真实 embedding、真实 LLM、网络或外部命令。

## 7. 实施顺序

1. 保留已完成且双审通过的 RRF、规则精排和 LLM 协议；按本设计将 Router 的纯规则默认选路替换为 LLM 提议与强制校验。
2. 实现 BM25 索引/检索及其离线测试。
3. 定义并测试 embedding/vector 的上游适配边界；在队友实现可用前，用 Fake provider/store 验收。
4. 将 ResearchMemory 从“同 Scope 已发布记忆筛选”扩展为可按 query 参与的证据通道。
5. 接入多路路由、RRF、精排，运行专项与全量回归，更新进度表和中文阶段讲解。

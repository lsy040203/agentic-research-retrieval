# C1 Agentic 路由与重排指南

## 目标与边界

C1 将多路只读检索置于 `RetrievalRouter` 的强制准入控制之下。LLM 只能提议工具计划，不能直接调用工具、放宽 Scope 或绕过预算。所有证据均受五维 `ScopeKey` 隔离。

```text
query + ScopeKey
      |
      v
LLMQueryPlanner (只提议 tool_rounds；失败 -> RuleQueryPlanner)
      |
      v
RetrievalRouter 强制校验
  白名单 / local Provider / read_only / Scope / 新鲜度 / 6轮12次预算
      |
      +--> research-memory  已发布且策略允许的 ResearchMemory
      +--> grep             本地文本定位
      +--> BM25             本地词法索引
      +--> vector           上游 embedding + 本地向量索引
      |
      v
HybridRetriever: RRF(k=60)
      |
      v
RuleReranker -> SiliconFlow LLM 精排（可选；失败回退 RuleReranker）
      |
      v
RouterResult: evidence, partial, traces, rejections, degradations
```

## 检索、融合与分数

- `bm25_score`：BM25 对查询词频、逆文档频率和文档长度的词法相关性分数。BM25 索引按 Scope 单独持久化；Scope 不符、索引失效或数据无效会返回空候选。
- `vector_score`：向量检索相似度。Retriever 必须验证 embedding、同一 embedding 模型、有限分数和同一 Scope；上游异常或任一验证失败安全返回空列表。
- `rrf_score`：至少两条实际有候选的通道才写入。RRF 将每一路名次按 `1 / (60 + rank)` 累加，避免不同检索器原始分数不可比；单路结果采用稳定确定性排序，不伪造 RRF 分数。
- `rerank_score` 与 `rerank_reason`：规则精排按查询词覆盖、归一化 RRF 与证据完整度给分并说明理由。LLM 成功时由模型返回 0 到 1 的分数和理由；候选最多 20 个，且先按 `chunk_id` 去重并验证同 ID 身份一致。

## 规划、轨迹与降级

LLM Planner 只收到已注册工具的名称和安全能力摘要，返回版本化 JSON 计划。Router 对其输出重新验证：未知工具、非本地 Provider、非只读工具、过期索引、Scope 不符和超预算都会拒绝，不能因 LLM 建议而放行。

- `traces`：每次规划或工具调度的非敏感记录，包含轮次、工具名、是否接受、候选数量、耗时和原因；不保存查询正文或密钥。
- `rejections`：未注册、Provider/只读/Scope/新鲜度/预算等准入拒绝原因。
- `degradations`：Planner 解析或配置错误、单工具失败、融合或精排失败等脱敏降级原因。单路失败不会取消其他已验证通道。
- `partial=true`：工具异常、无效结果、Scope 不一致、连续没有新增证据或预算耗尽时为真；已经取得的安全证据仍会返回。

## LLM 精排与环境变量

默认 LLM 关闭，因此离线场景确定性使用规则规划和规则精排。启用可选的 SiliconFlow 兼容精排/规划时，读取以下环境变量：

```powershell
$env:ARR_LLM_ENABLED = "true"
$env:ARR_SILICONFLOW_API_KEY = "<在本机设置，不要写入源码或日志>"
$env:ARR_LLM_MODEL = "<模型名>"
$env:ARR_LLM_TIMEOUT_SECONDS = "10"
```

密钥只从 `ARR_SILICONFLOW_API_KEY` 读取，不应写入配置打印、Prompt、trace、异常或文档。允许端点仅为精确的 `https://api.siliconflow.cn/v1`（最终请求为其 `/chat/completions`）；非 HTTPS、本地主机、其他主机、端口、query、fragment、用户名密码和重定向均拒绝。缺少开关、模型、密钥或端点校验失败，以及超时、HTTP 错误、非法 JSON、重复/漏失 ID 或非法分数，都会记录降级码并回退规则精排。

## ResearchMemory 检索法与测试

`ResearchEvidenceRetriever` 先以 Scope 调用 `ResearchStore.list_published(scope)`，再经过 `ResearchPolicy.filter_and_rank` 过滤生命周期、环境适用性和冲突；只把标题或正文与查询 token 有交集的已发布记忆转换成 `EvidenceChunk`。其 `source_ref` 为 `research_memory:<memory_id>`，可保留适用性中的定位信息，供后续追溯。

所有 C1 测试使用 Fake embedding、Fake vector store、Fake HTTP transport 或 Fake LLM client，不调用真实服务。2026-07-29 实测：

```text
专项：180 passed in 0.31s
全量：408 passed, 2 skipped in 1.15s
```

两项跳过均是 Windows 未授予创建符号链接权限而触发的 `grep` 测试环境限制，与 C1 无关。

# 简历项目说明：ARR Agentic Research Retrieval

以下内容基于当前仓库实现和 2026-08-12 的本地测试结果整理；没有把规划中的 Web 检索、真实向量后端、公开 API 或线上业务指标写成已完成能力。

## 可直接放入简历的版本

**ARR（Agentic Research Retrieval）｜个人项目｜Python / FastAPI / SQLite / BM25 / RRF / LLM Rerank**

**背景：** 面向科研工作中的文献、实验记录与代码证据检索，解决纯关键词定位难覆盖跨概念问题、LLM 自主工具调用缺少权限边界和结果不可审计的问题。

**目标：** 构建本地优先的多路证据检索模块，让 LLM 只负责提出检索与重排建议，由确定性路由器强制执行 Scope、只读性、索引新鲜度和预算约束。

- 设计五维 `ScopeKey`（团队、项目、仓库、分支、实验环境）隔离模型，贯穿研究记忆、grep、BM25、向量召回与结果校验，防止跨研究上下文的证据混入。
- 实现受控 Agentic Router：`LLMQueryPlanner` 基于 query 与工具能力摘要生成多轮 `tool_rounds`，自主选择 ResearchMemory、grep、BM25、向量中的一至多路检索；Router 二次校验工具白名单、`local` Provider、只读属性、索引新鲜度及最多 6 轮/12 次调用预算，异常自动回退规则规划。
- 构建 ResearchMemory、受信目录 grep、BM25、向量适配器四路召回；将 LLM 选中且通过准入的多路候选使用 RRF（`k=60`）融合，并以确定性规则或可选 LLM 对最多 20 个候选精排。
- 实现精排安全回退机制：LLM 默认关闭；对端点、凭据来源、JSON 结构、候选 ID 完整性与分数范围进行校验，超时或非法响应时保留规则排序结果。
- 建立 `ToolTrace`、拒绝原因、降级原因与 `partial` 状态的可观测返回；grep 检索限制受信根目录并跳过符号链接、敏感文件和疑似凭据内容。
- 构建脱敏本地金标评测，计算 `Recall@K`、`MRR`、`scope_leak_count`；完成 Fake 审批/回执 E2E，验证“记录审批与审计、不执行命令或发布”的安全边界。

**结果：** 2026-08-12 本地全量回归 `564 passed, 2 skipped`；两个跳过项仅由 Windows `WinError 1314` 的符号链接创建权限限制触发。对 10 个合成脱敏场景的受控真实 LLM 对比中，`arr-router-v2` 保持 `scope_leak_count=0`，但仍有 2 次超时；该结果仅说明协议稳定性和降级表现，不代表真实业务命中率。

## 一页简历精简版

**ARR Agentic Research Retrieval｜个人项目**

- 设计五维 Scope 隔离的科研证据检索架构，统一约束 ResearchMemory、grep、BM25 与向量召回，确保候选与请求研究上下文一致。
- 实现受控 Agentic Router：LLM 依据 query 自主选择并编排 ResearchMemory、grep、BM25、向量等多路检索，Router 负责白名单、只读、索引新鲜度和 6 轮/12 次预算校验，失败自动降级规则规划。
- 对 LLM 选择且通过准入的多路候选采用 RRF（k=60）融合，并实现规则优先的 LLM Rerank；对超时、非法 JSON 和候选 ID 篡改安全回退。
- 建立 `Recall@K` / `MRR` / `scope_leak_count` 离线评测与可观测 Trace；本地全量回归 `564 passed, 2 skipped`。

## 面试时的 30 秒解释

这是一个面向科研工作流的“受控 Agentic RAG 检索层”。LLM 会基于 query 和已注册工具的能力摘要，生成多轮计划，自主选择研究记忆、grep、BM25、向量中的一到多条检索通道；Router 再强制校验作用域、只读性、索引新鲜度和预算。通过准入的多路候选使用 RRF 融合，最后由规则或受限 LLM 精排；任何模型超时或输出不可信时都会保留已验证的规则结果，并返回降级和审计轨迹。评测侧重点是 Recall、MRR 以及跨 Scope 泄露数，而非虚构的线上问答准确率。

## 技术关键词

`Agentic RAG`、`Tool Planning`、`Policy Enforcement`、`Scope Isolation`、`BM25`、`RRF`、`Reranking`、`Graceful Fallback`、`SQLite`、`FastAPI`、`pytest`、`Offline Evaluation`、`Audit Trace`

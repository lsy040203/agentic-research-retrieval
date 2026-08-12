# B1 本地证据与研究记忆检索指南

## 已实现范围

B1 在完整五维 `ScopeKey`（团队、项目、仓库、分支、实验环境）内提供两条只读检索路径：已发布研究记忆的召回，以及可信本地目录中的行级文本证据搜索。它不写入目标工作区、不执行命令、不访问网络。

## 来源与安全边界

`ResearchRetriever` 只通过 `ResearchStore.list_published(scope)` 读取当前作用域的已发布候选，既不直接查询 SQLite，也不修改研究记忆。

`GrepRetriever` 只接受构造时已解析的现有目录作为可信根。搜索时拒绝空查询、绝对路径、根目录逃逸和路径中的符号链接；仅读取根目录内的普通 UTF-8 文件，并受文件数、结果数和单文件字节数限制。它跳过二进制文件、`.env` 或明显的凭据命名文件，以及含密钥/令牌/授权头模式的文件；实现使用 `pathlib`、`os` 与 Python 正则，绝不启动子进程。

返回的本地证据是 `EvidenceChunk`，其 `source_ref` 为 POSIX 相对路径，`locator` 为 `line:<number>`，并含 `retriever: "python-grep"` 和查询词元数据。

## 组件职责与调用流

1. `ResearchPolicy`：纯函数式地检查五维作用域、`PUBLISHED` 生命周期与实验环境适用性；同一非空 `conflict_key` 只保留确定性最优候选。
2. `ResearchRetriever`：将存储层返回的候选完整交给策略层，不承担 SQLite、网络或写入职责。
3. `GrepRetriever`：在可信本地目录内完成安全过滤和逐行、大小写无关的文本匹配。

研究记忆流为：`ScopeKey` -> `ResearchStore.list_published` -> `ResearchPolicy.filter_and_rank` -> 可追溯候选。文件证据流为：查询与相对路径 -> 根目录/文件安全检查 -> 行级匹配 -> `EvidenceChunk`。

## 置信度计算

`ResearchPolicy.retrieval_confidence` 将结果裁剪到 `[0, 1]`，固定公式为：

`0.35 × 来源可靠性 + 0.25 × 发布状态 + 0.20 × 环境匹配 + 0.10 × 新鲜度 + 0.10 × 证据完整度`。

来源可靠性按有效 `source_refs` 计分（无来源为 0，单来源为 0.6，至少两个为 1）；发布和环境分量只有满足条件时为 1。新鲜度在更新后 183 天内为 1，之后在 365 天内线性衰减；证据完整度检查来源、定位、验证日志和实验 ID。冲突组优先原始记忆置信度、更晚的 UTC `updated_at`、更小的 `memory_id`，以保证结果稳定。

## 测试证据

2026-07-28 的 B1 定向回归：

```powershell
C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest tests\test_research_policy.py tests\test_research_retriever.py tests\test_grep_retriever.py tests\test_research_store.py -q
```

结果为 `48 passed, 2 skipped in 0.33s`。全量回归命令 `C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q` 的结果为 `229 passed, 2 skipped in 0.94s`。两个跳过项是 Windows 环境中创建符号链接受 `WinError 1314` 权限限制；对应测试在可创建链接的环境中验证链接拒绝和遍历跳过行为。

## 后续 C1

C1 将在 B1 候选契约之上实现只读检索路由、预算与轨迹记录、向量/BM25 候选归一化、RRF 融合和本地重排的确定性回退。C1 尚未实现，B1 不提供 Router、向量检索、RRF 或 API。

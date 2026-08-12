# 证据驱动科研工作区多 Agent 产品设计

**状态：已确认，待实施计划**

## 1. 产品定位

本产品面向需要长期推进研究项目的科研人员。它以通用科研流程为骨架，通过领域档案适配 AI/ML、生命科学、材料科学等不同领域；首个落地子场景聚焦 **AI/ML 的论文—代码—实验复现协同**。

产品不是只回答问题的聊天 Agent，也不是自动做科研的执行器。它帮助用户将研究想法、文献证据、科研代码、实验结果、审查意见和失败经验沉淀为可追溯、可复用、可撤销的本地项目资产。

ARR 是该产品的记忆、检索、证据校验、生命周期、审批和验证底座。它不替代科研流程本身。

## 2. 目标用户与核心问题

目标用户包括博士生、科研人员、AI/ML 研究者、独立研究者、使用 Claude Code/Codex 的科研开发者，以及并行维护多个研究项目的人员。

系统必须能够帮助用户回答：

- 这个 Idea 上次讨论到哪里了？
- 这个实验为什么失败？
- 这个指标来自哪个结果文件、配置、代码版本和环境？
- 这个结论是否已经过审查？
- 我是否在重复一个已经被证明不可行的方向？
- AI 的建议如何转变为稳定的项目资产，而不是散落在聊天记录中？

## 3. 通用科研状态机

```text
Research Goal
  -> Domain Build
  -> Idea Card
  -> Experiment Design
  -> Review Gate
  -> External Run
  -> Metrics & Evidence
  -> Analysis
  -> Iterate / Promote / Archive
```

首个 AI/ML 子流程为：

```text
论文 / 研究想法
  -> 方法、数据、指标与复现条件提取
  -> 检索本地代码、配置、日志、历史实验与失败案例
  -> 判断已实现 / 部分实现 / 未实现 / 证据不足
  -> 生成待审查的复现实验计划
  -> 人工批准后由外部执行器实施
  -> 回执关联指标、日志、配置与产物
  -> 迭代、推进论文主线或归档失败方向
```

## 4. 多 Agent 职责

```text
研究者（最终 PI）
   |
   v
Research Orchestrator / 科研总管
   |
   +--> Domain Builder：建立领域档案、资源约束、成功标准和审查红线
   +--> Research Memory Steward：维护项目状态、Idea、决策与失败归档
   +--> Literature Evidence Agent：发现、核验和整理文献
   +--> Code Research Agent：定位代码、配置、commit 与实验入口
   +--> Experiment Designer：生成 baseline、ablation、指标、风险与 fallback
   +--> Reviewer：审查证据、冲突、环境与成功判据
   +--> Execution Coordinator：只转交已批准的冻结计划给外部执行器
   +--> Result Analyst：关联指标、日志、配置和代码版本，分析结果
   +--> Archivist：推进、迭代、归档、撤销或创建候选记忆
```

各角色共享 ARR 的五维 Scope、ResearchMemory、检索路由、RRF/重排、生命周期、审批包和验证回执能力。角色分工不代表每一步都必须调用 LLM；Scope、权限、生命周期和批准校验应由程序强制执行。

## 5. 工作区与资产模型

```text
研究工作区 Workspace
  └─ 领域档案 Domain Profile
      ├─ Project A
      │   ├─ Idea Cards
      │   ├─ 文献与证据
      │   ├─ 代码与实验资产
      │   ├─ 审查与决策记录
      │   └─ 已沉淀 / 已归档记忆
      └─ Project B
```

资产状态：

```text
Idea Card:
draft -> exploring -> experiment_designed -> promoted / archived

Experiment Card:
draft -> under_review -> approved -> running
      -> analyzed -> iterate / promote / archive

Research Claim:
draft -> evidence_needed -> under_review
      -> accepted / withheld / revoked

Failure Archive:
observed -> analyzed -> archived -> reopened
```

每项资产应关联来源、代码 commit/分支、配置、数据版本、实验环境、审查状态、验证证据、生命周期和失效条件。

## 6. 交互方式与持久化规则

产品采用 **Chat-first + 本地科研工作区 + 只读 Dashboard**：

- 对话是主要操作入口；用户可用自然语言提出研究目标、读论文、设计实验、分析失败或查询项目状态。
- 本地工作区承载真实科研资产；Dashboard 仅展示状态、证据与关系，不能绕过 Agent/政策层直接修改资产。
- `/build` 用于创建或更新领域档案，包括研究领域、实验资源、论文目标、审查红线、结果意义与证据源规则。

默认不持久化 AI 的中间建议。正确交互为：

```text
用户与 AI 迭代讨论
  -> AI 提供草案、证据和可保存建议
  -> 用户明确确认最终版本
  -> 保存最终科研资产、关键决策摘要和证据引用
```

系统不保存完整聊天原文，以避免中间推测、无效建议和敏感内容污染长期项目记忆。保存、更新、归档、撤销和删除均需用户明确触发，并产生审计记录。

## 7. 文献、审查与执行边界

联网文献发现允许使用 Google Scholar、arXiv、PubMed、出版社或会议网站等来源；Google Scholar 仅用于发现候选。

正式证据必须具有 DOI、arXiv ID、PubMed ID 或官方出版社/会议页面等可核验标识，并保存标题、作者、年份、venue、永久链接、检索时间和原始来源。LLM 只能使用工具返回且核验成功的元数据，不得自行编造或补全论文信息。

文献状态：

```text
verified_peer_reviewed  已正式发表
verified_preprint       已核验预印本，可引用但必须标注
unverified              不可作为正式证据或长期记忆
retracted               不可作为支持性证据
```

涉及代码修改、下载依赖、运行训练、提交代码或发布的操作必须：

```text
待审计划 -> Reviewer 检查 -> 人工批准 -> 外部执行器执行
         -> Verification Receipt -> 推进 / 归档 / 候选沉淀
```

ARR 不自行执行命令、联网下载、修改工作区或自动发布 ResearchMemory。

## 8. 非目标与后续范围

- 本阶段不实施评测模块和评测数据集；相关工作后续单独设计。
- 不将系统宣传为自动完成科学发现或替代研究者的科学判断。
- 不自动把 LLM 总结、未经核验论文或单次实验结果升级为可信结论。
- 不允许跨项目、仓库、分支或实验环境无约束复用记忆与实验结论。


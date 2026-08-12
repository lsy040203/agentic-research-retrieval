
"""
全局枚举常量定义

该文件定义 OS Agent Memory 系统中的核心枚举类型。

覆盖比赛要求：
1. 多源数据接入：对话、工具调用、工具结果、用户行为、手动配置、任务轨迹等
2. 多类型记忆：偏好、知识、工作流、案例、模板、工具经验、环境配置、安全策略等
3. 短期 / 中期 / 长期记忆流转
4. 记忆生命周期：待处理、有效、被替代、删除、过期、归档
5. 冲突处理：覆盖、合并、场景隔离、保留旧版本
6. 遗忘机制：自然语言精准遗忘、按类型遗忘、按场景遗忘
7. 安全机制：敏感信息检测、脱敏、拒绝写入
8. 检索机制：关键词检索、向量检索、混合检索、重排序
9. 评测机制：抽取、检索、冲突、遗忘、延迟、安全、端到端评测
"""

from enum import Enum


class EventType(str, Enum):
    """
    事件类型。

    EventType 表示 Memory 系统接收的原始事件类型。
    它对应 OS Agent 运行过程中产生的多源数据。
    """

    # 用户与 Agent 的自然语言对话
    CONVERSATION = "conversation"

    # Agent 调用工具前产生的事件
    TOOL_CALL = "tool_call"

    # 工具执行完成后的结果事件
    TOOL_RESULT = "tool_result"

    # 用户行为事件，例如点击、打开文件、切换应用、修改设置等
    USER_BEHAVIOR = "user_behavior"

    # 用户手动配置，例如用户主动设置偏好、权限、安全策略等
    USER_CONFIG = "user_config"

    # Agent 的任务规划事件，例如计划步骤、子任务拆解等
    TASK_PLAN = "task_plan"

    # Agent 的任务执行轨迹，例如每一步执行记录
    TASK_TRACE = "task_trace"

    # 任务完成后的总结事件
    TASK_SUMMARY = "task_summary"

    # 系统环境事件，例如当前操作系统、软件版本、文件路径、应用状态等
    SYSTEM_CONTEXT = "system_context"

    # 外部文档或知识导入事件
    DOCUMENT_IMPORT = "document_import"

    # 用户主动反馈事件，例如“这个答案不对”“以后不要这样”
    USER_FEEDBACK = "user_feedback"


class EventSource(str, Enum):
    """
    事件来源。

    EventSource 比 EventType 更偏向数据来源通道。
    EventType 表示事件是什么，EventSource 表示事件从哪里来。
    """

    OS_AGENT = "os_agent"
    MOCK_AGENT = "mock_agent"
    CHAT_UI = "chat_ui"
    TOOL_RUNTIME = "tool_runtime"
    SYSTEM_MONITOR = "system_monitor"
    USER_CONFIG_PANEL = "user_config_panel"
    FILE_IMPORTER = "file_importer"
    EVALUATION_DATASET = "evaluation_dataset"


class ActorType(str, Enum):
    """
    事件主体。

    表示某条事件由谁产生。
    """

    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    SYSTEM = "system"
    ADMIN = "admin"


class MemoryType(str, Enum):
    """
    记忆类型。

    MemoryType 表示长期记忆中保存的信息类别。
    """

    # 用户偏好，例如“用户偏好 PDF 导出”“用户喜欢简洁回复”
    PREFERENCE = "preference"

    # 知识记忆，例如工具能力、文档知识、领域知识
    KNOWLEDGE = "knowledge"

    # 工作流记忆，例如“月报流程：读取数据 -> 生成文档 -> 导出 PDF”
    WORKFLOW = "workflow"

    # 历史案例，例如过去成功完成的任务案例
    CASE = "case"

    # 可复用模板，例如月报模板、邮件模板、命令模板
    TEMPLATE = "template"

    # 工具经验，例如工具成功率、失败原因、适用场景、参数习惯
    TOOL = "tool"

    # 环境记忆，例如常用目录、系统配置、软件状态
    ENVIRONMENT = "environment"

    # 安全策略，例如禁止上传敏感文件、不保存密码
    SAFETY = "safety"

    # 用户画像，例如角色、工作习惯、常见任务类型
    PROFILE = "profile"

    # 任务状态或项目状态，通常用于中期记忆
    TASK_STATE = "task_state"

    # 会话摘要，通常用于中期记忆
    SESSION_SUMMARY = "session_summary"


class MemoryLevel(str, Enum):
    """
    记忆层级。

    用于区分短期、中期、长期记忆。
    """

    # 当前会话 / 当前任务上下文，例如消息历史、工具轨迹、当前任务状态
    SHORT_TERM = "short_term"

    # 会话级 / 任务级 / 项目阶段摘要
    MID_TERM = "mid_term"

    # 跨会话长期保存的稳定偏好、知识、工作流、案例、模板等
    LONG_TERM = "long_term"


class MemoryStatus(str, Enum):
    """
    记忆状态。

    用于记忆生命周期管理。
    """

    # 候选或待确认状态
    PENDING = "pending"

    # 当前有效
    ACTIVE = "active"

    # 已被新版本替代
    SUPERSEDED = "superseded"

    # 用户要求遗忘或系统删除
    DELETED = "deleted"

    # 超过生命周期后失效
    EXPIRED = "expired"

    # 已归档，默认不参与检索，但可追溯
    ARCHIVED = "archived"

    # 被安全策略拒绝写入
    REJECTED = "rejected"


class Scene(str, Enum):
    """
    场景。

    用于区分不同任务场景，支持场景隔离检索和场景化偏好。
    """

    # 全局场景
    GLOBAL = "global"

    # 办公场景，例如 WPS、文档、表格、月报、邮件
    OFFICE = "office"

    # 编程场景
    CODING = "coding"

    # 系统操作场景，例如文件管理、系统设置、应用启动
    SYSTEM = "system"

    # 浏览器和网页场景
    BROWSER = "browser"

    # 文件处理场景
    FILE = "file"

    # 邮件场景
    EMAIL = "email"

    # 数据分析场景
    DATA_ANALYSIS = "data_analysis"

    # 未知或待分类场景
    UNKNOWN = "unknown"


class ConfidenceLevel(str, Enum):
    """
    置信度等级。

    可用于展示、规则判断或候选记忆晋升。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConflictType(str, Enum):
    """
    冲突类型。

    用于新旧记忆冲突检测。
    """

    # 没有冲突
    NONE = "none"

    # 同一 key 下内容相反，例如 PDF 偏好改成 Word 偏好
    CONTRADICTION = "contradiction"

    # 新记忆是旧记忆的补充
    COMPLEMENT = "complement"

    # 新记忆和旧记忆重复
    DUPLICATE = "duplicate"

    # 新记忆更具体，旧记忆更泛化
    SPECIALIZATION = "specialization"

    # 新记忆更泛化，旧记忆更具体
    GENERALIZATION = "generalization"

    # 不同场景下看似冲突，实际应该场景隔离
    SCENE_SPECIFIC = "scene_specific"


class ConflictResolution(str, Enum):
    """
    冲突处理策略。
    """

    # 不处理，保留原记忆
    KEEP_OLD = "keep_old"

    # 使用新记忆覆盖旧记忆，旧记忆变 superseded
    REPLACE = "replace"

    # 合并新旧记忆
    MERGE = "merge"

    # 保留新旧两条，但按场景隔离
    KEEP_BOTH_BY_SCENE = "keep_both_by_scene"

    # 降低置信度，等待更多证据
    DOWNGRADE_CONFIDENCE = "downgrade_confidence"

    # 标记为待确认
    REQUIRE_CONFIRMATION = "require_confirmation"

    # 拒绝新记忆
    REJECT_NEW = "reject_new"


class ForgetScope(str, Enum):
    """
    遗忘范围。

    用于自然语言遗忘指令解析。
    """

    # 遗忘某一条具体记忆
    MEMORY = "memory"

    # 按记忆类型遗忘，例如遗忘所有偏好记忆
    MEMORY_TYPE = "memory_type"

    # 按场景遗忘，例如遗忘办公场景下的偏好
    SCENE = "scene"

    # 按关键词遗忘，例如遗忘 PDF 相关记忆
    KEYWORD = "keyword"

    # 按时间范围遗忘
    TIME_RANGE = "time_range"

    # 遗忘某个会话
    SESSION = "session"

    # 遗忘某个任务
    TASK = "task"

    # 用户全部记忆
    USER_ALL = "user_all"


class ForgetMode(str, Enum):
    """
    遗忘模式。
    """

    # 软删除：将状态改为 deleted，保留审计日志
    SOFT_DELETE = "soft_delete"

    # 硬删除：从存储中物理删除，慎用
    HARD_DELETE = "hard_delete"

    # 从检索索引中失效，但保留结构化记录
    INDEX_ONLY_DELETE = "index_only_delete"

    # 匿名化或脱敏，而不是直接删除
    ANONYMIZE = "anonymize"


class SensitiveType(str, Enum):
    """
    敏感信息类型。

    用于敏感信息识别、过滤、脱敏和安全评测。
    """

    PHONE = "phone"
    ID_CARD = "id_card"
    BANK_CARD = "bank_card"
    PASSWORD = "password"
    TOKEN = "token"
    API_KEY = "api_key"
    PRIVATE_KEY = "private_key"
    EMAIL = "email"
    ADDRESS = "address"
    FILE_PATH = "file_path"
    PERSONAL_NAME = "personal_name"
    SECRET_DOCUMENT = "secret_document"
    UNKNOWN = "unknown"


class SecurityAction(str, Enum):
    """
    安全处理动作。
    """

    # 允许写入
    ALLOW = "allow"

    # 脱敏后写入
    MASK = "mask"

    # 拒绝写入长期记忆
    REJECT = "reject"

    # 需要用户确认
    REQUIRE_CONFIRMATION = "require_confirmation"

    # 仅保存在短期记忆，不进入长期记忆
    SHORT_TERM_ONLY = "short_term_only"


class RetrievalMode(str, Enum):
    """
    检索模式。
    """

    # 关键词检索，例如 BM25、SQLite FTS
    KEYWORD = "keyword"

    # 向量检索
    VECTOR = "vector"

    # 关键词 + 向量 + 元数据过滤
    HYBRID = "hybrid"

    # 精确 key 查询
    EXACT_KEY = "exact_key"

    # 按场景 / 类型 / 状态过滤查询
    FILTER = "filter"


class RerankStrategy(str, Enum):
    """
    重排序策略。
    """

    # 不重排
    NONE = "none"

    # 按相关性分数
    SCORE = "score"

    # 场景匹配优先
    SCENE_MATCH = "scene_match"

    # 记忆类型加权
    MEMORY_TYPE_WEIGHT = "memory_type_weight"

    # 时间衰减
    TIME_DECAY = "time_decay"

    # 置信度加权
    CONFIDENCE_WEIGHT = "confidence_weight"

    # 安全策略优先
    SAFETY_FIRST = "safety_first"

    # 综合策略
    HYBRID = "hybrid"


class PromotionReason(str, Enum):
    """
    记忆晋升原因。

    表示为什么一条信息可以从短期 / 中期进入长期记忆。
    """

    # 用户明确表达偏好
    EXPLICIT_PREFERENCE = "explicit_preference"

    # 用户多次重复行为
    REPEATED_BEHAVIOR = "repeated_behavior"

    # 工具执行成功且可复用
    TOOL_SUCCESS = "tool_success"

    # 任务流程可复用
    REUSABLE_WORKFLOW = "reusable_workflow"

    # 用户主动配置
    USER_CONFIGURED = "user_configured"

    # 用户明确要求记住
    USER_REQUESTED = "user_requested"

    # 高置信度知识
    HIGH_CONFIDENCE_KNOWLEDGE = "high_confidence_knowledge"


class AgentPhase(str, Enum):
    """
    Agent 运行阶段。

    用于 Memory Policy 判断何时写事件、检索记忆、抽取记忆、压缩上下文。
    """

    # 收到用户消息
    USER_TURN = "user_turn"

    # 任务理解
    TASK_UNDERSTANDING = "task_understanding"

    # 任务规划
    PLANNING = "planning"

    # 工具选择
    TOOL_SELECTION = "tool_selection"

    # 工具调用前
    BEFORE_TOOL_CALL = "before_tool_call"

    # 工具调用后
    AFTER_TOOL_RESULT = "after_tool_result"

    # 生成回复前
    BEFORE_RESPONSE = "before_response"

    # 任务结束
    TASK_FINISH = "task_finish"

    # 上下文压缩前
    BEFORE_CONTEXT_COMPACTION = "before_context_compaction"

    # 用户要求遗忘
    FORGET_REQUEST = "forget_request"


class PolicyAction(str, Enum):
    """
    Memory Policy 动作。

    表示策略层决定要做什么。
    """

    # 写入事件
    WRITE_EVENT = "write_event"

    # 检索记忆
    RETRIEVE_MEMORY = "retrieve_memory"

    # 抽取候选记忆
    EXTRACT_MEMORY = "extract_memory"

    # 晋升为长期记忆
    PROMOTE_MEMORY = "promote_memory"

    # 压缩短期上下文为中期摘要
    SUMMARIZE_SESSION = "summarize_session"

    # 执行遗忘
    FORGET_MEMORY = "forget_memory"

    # 不做任何记忆操作
    NO_OP = "no_op"


class EvaluationTask(str, Enum):
    """
    评测任务类型。
    """

    # 偏好抽取评测
    PREFERENCE_EXTRACTION = "preference_extraction"

    # 知识抽取评测
    KNOWLEDGE_EXTRACTION = "knowledge_extraction"

    # 工作流抽取评测
    WORKFLOW_EXTRACTION = "workflow_extraction"

    # 检索评测
    RETRIEVAL = "retrieval"

    # 冲突处理评测
    CONFLICT_RESOLUTION = "conflict_resolution"

    # 遗忘评测
    FORGETTING = "forgetting"

    # 安全过滤评测
    SECURITY = "security"

    # 延迟评测
    LATENCY = "latency"

    # 端到端任务评测
    END_TO_END = "end_to_end"


class MetricName(str, Enum):
    """
    评测指标名称。
    """

    PRECISION = "precision"
    RECALL = "recall"
    F1 = "f1"

    RECALL_AT_K = "recall_at_k"
    MRR = "mrr"
    NDCG = "ndcg"

    CONFLICT_ACCURACY = "conflict_accuracy"

    FORGET_SUCCESS_RATE = "forget_success_rate"
    OVER_DELETE_RATE = "over_delete_rate"
    UNDER_DELETE_RATE = "under_delete_rate"

    SENSITIVE_DETECTION_PRECISION = "sensitive_detection_precision"
    SENSITIVE_DETECTION_RECALL = "sensitive_detection_recall"

    LATENCY_AVG_MS = "latency_avg_ms"
    LATENCY_P50_MS = "latency_p50_ms"
    LATENCY_P95_MS = "latency_p95_ms"
    LATENCY_P99_MS = "latency_p99_ms"

    MEMORY_ON_SUCCESS_RATE = "memory_on_success_rate"
    MEMORY_OFF_SUCCESS_RATE = "memory_off_success_rate"
    IMPROVEMENT_RATE = "improvement_rate"


class StorageBackend(str, Enum):
    """
    存储后端类型。
    """

    # SQLite 结构化存储
    SQLITE = "sqlite"

    # Markdown 快照，便于展示和调试
    MARKDOWN = "markdown"

    # 银河麒麟系统向量数据库 SDK
    KYLIN_VECTOR_DB = "kylin_vector_db"

    # 开发或对照实验使用
    FAISS = "faiss"

    # 单元测试使用
    MOCK = "mock"


class EmbeddingBackend(str, Enum):
    """
    Embedding 后端类型。
    """

    # 银河麒麟系统 Embedding 接口
    KYLIN_EMBEDDING = "kylin_embedding"

    # 本地 mock embedding，开发和测试使用
    MOCK = "mock"

    # 其他本地 embedding，开发环境备用
    LOCAL = "local"


class ApiRouteTag(str, Enum):
    """
    API 路由标签。
    """

    EVENTS = "events"
    MEMORIES = "memories"
    RETRIEVAL = "retrieval"
    FORGETTING = "forgetting"
    EVALUATION = "evaluation"
    HEALTH = "health"


class ResearchMemoryKind(str, Enum):
    """Kinds of memory managed by the ARR research domain."""

    KNOWLEDGE = "knowledge"
    LITERATURE = "literature"
    EXPERIMENT = "experiment"
    WORKFLOW = "workflow"
    PREFERENCE = "preference"
    RESEARCH_CASE = "research_case"


class ResearchMemoryStatus(str, Enum):
    """Lifecycle states for a research memory."""

    CANDIDATE = "candidate"
    VERIFIED = "verified"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"
    CONFLICT = "conflict"
    EXPIRED = "expired"


class ApprovalStatus(str, Enum):
    """Lifecycle states for approval packages."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RiskLevel(str, Enum):
    """Risk levels attached to an approval package."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalDecision(str, Enum):
    """Decisions an approver may record for a package."""

    APPROVED = "approved"
    REJECTED = "rejected"


class VerificationStatus(str, Enum):
    """Outcomes for verification runs."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


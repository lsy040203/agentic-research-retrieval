"""
全局配置
"""
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 内存存储路径
MEMORY_STORE_PATH = PROJECT_ROOT / "memory_store"
MEMORY_STORE_PATH.mkdir(exist_ok=True)

# 数据路径
DATA_RAW_PATH = PROJECT_ROOT / "data" / "raw"
DATA_GOLD_PATH = PROJECT_ROOT / "data" / "gold"
DATA_PROCESSED_PATH = PROJECT_ROOT / "data" / "processed"

# SQLite 配置
DEFAULT_DB_PATH = MEMORY_STORE_PATH / "users" / "{user_id}" / "memories.db"
DEFAULT_EVENTS_DB_PATH = MEMORY_STORE_PATH / "users" / "{user_id}" / "events.db"
DEFAULT_RESEARCH_MEMORY_DB_PATH = MEMORY_STORE_PATH / "research_memory.db"

# 检索参数
RETRIEVAL_TOP_K = 5
CONFIDENCE_THRESHOLD = 0.5

# MVP 演示数据文件
DEMO_DATA_PATH = DATA_RAW_PATH / "office_demo_events.jsonl"

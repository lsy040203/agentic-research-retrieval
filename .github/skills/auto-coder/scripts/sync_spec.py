"""Export DEV_SPEC.md chapters into auto-coder reference files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SPEC = ROOT / "DEV_SPEC.md"
REFERENCES = ROOT / ".github" / "skills" / "auto-coder" / "references"
CHAPTERS = {
    "01-overview.md": "项目概述与设计原则",
    "02-features.md": "功能边界与阶段范围",
    "03-tech-stack.md": "技术架构与数据模型",
    "04-testing.md": "测试与验收标准",
    "05-architecture.md": "模块与文件结构",
    "06-schedule.md": "实施排期与依赖",
    "07-future.md": "未来范围",
}


def section(markdown: str, heading: str) -> str:
    pattern = rf"(?ms)^## {re.escape(heading)}\s*$.*?(?=^## |\Z)"
    match = re.search(pattern, markdown)
    if match is None:
        raise ValueError(f"Missing required heading: {heading}")
    return match.group(0).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.parse_args()
    markdown = SPEC.read_text(encoding="utf-8")
    REFERENCES.mkdir(parents=True, exist_ok=True)
    for filename, heading in CHAPTERS.items():
        (REFERENCES / filename).write_text(section(markdown, heading), encoding="utf-8")
    print(f"Synced {len(CHAPTERS)} reference files from {SPEC}")


if __name__ == "__main__":
    main()

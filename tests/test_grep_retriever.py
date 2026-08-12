"""纯 Python 本地文本证据检索器的安全边界测试。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from retrieval.grep_retriever import GrepRetriever


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    """返回目录中每个普通文件的内容快照，用于确认检索没有写入。"""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_search_returns_line_located_evidence_without_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常文本命中返回可追溯行号，且不允许启动子进程。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.txt").write_text("alpha\nResearch signal\n", encoding="utf-8")
    before = _tree_snapshot(root)

    def fail_popen(*args: object, **kwargs: object) -> object:
        raise AssertionError("GrepRetriever must not start a subprocess")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    chunks = GrepRetriever(root, max_results=5, max_files=10, max_file_bytes=4096).search(
        "research", relative_path="."
    )

    assert [(chunk.source_ref, chunk.locator) for chunk in chunks] == [
        ("notes.txt", "line:2")
    ]
    assert chunks[0].content == "Research signal"
    assert chunks[0].metadata == {"retriever": "python-grep", "query": "research"}
    assert _tree_snapshot(root) == before


@pytest.mark.parametrize("relative_path", ["../outside", "C:/outside"])
def test_search_rejects_absolute_and_escaping_relative_paths(
    tmp_path: Path, relative_path: str
) -> None:
    """绝对路径和逃出可信根目录的相对路径必须被拒绝。"""

    root = tmp_path / "allowed"
    root.mkdir()

    with pytest.raises(ValueError):
        GrepRetriever(root).search("query", relative_path=relative_path)


def test_search_skips_binary_sensitive_named_and_sensitive_content_files(
    tmp_path: Path,
) -> None:
    """二进制、敏感文件名和含密钥模式的文件不得泄露。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "binary.dat").write_bytes(b"research\x00signal")
    (root / ".env").write_text("research=secret", encoding="utf-8")
    (root / "credentials.txt").write_text("research secret", encoding="utf-8")
    (root / "api-notes.txt").write_text("api_key=secret\nresearch", encoding="utf-8")
    (root / "public.txt").write_text("research is public", encoding="utf-8")

    chunks = GrepRetriever(root).search("research")

    assert [chunk.source_ref for chunk in chunks] == ["public.txt"]


@pytest.mark.parametrize("directory", ["credentials", ".env"])
def test_search_skips_files_below_sensitive_ancestor_directories(
    tmp_path: Path, directory: str
) -> None:
    """敏感目录中的普通文件名也不得绕过来源筛除。"""

    root = tmp_path / "allowed"
    nested = root / directory
    nested.mkdir(parents=True)
    (nested / "research.txt").write_text("research signal", encoding="utf-8")

    assert GrepRetriever(root).search("research") == []


def test_search_honors_max_results(tmp_path: Path) -> None:
    """达到结果上限后停止返回更多匹配。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.txt").write_text("research one\nresearch two\n", encoding="utf-8")

    chunks = GrepRetriever(root, max_results=1).search("research")

    assert [(chunk.source_ref, chunk.locator) for chunk in chunks] == [
        ("notes.txt", "line:1")
    ]


def test_search_rejects_an_empty_query(tmp_path: Path) -> None:
    """空白查询没有明确检索意图，必须被拒绝。"""

    root = tmp_path / "allowed"
    root.mkdir()

    with pytest.raises(ValueError):
        GrepRetriever(root).search("   ")


def _create_symlink(link: Path, target: Path, *, is_directory: bool = False) -> None:
    """创建测试链接；Windows 未授予创建符号链接权限时跳过。"""

    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip(f"symbolic links unavailable in this environment: {error}")
        raise


def test_search_rejects_an_in_root_symlink_supplied_as_relative_path(
    tmp_path: Path,
) -> None:
    """即使链接目标仍在根目录内，也不得将链接作为请求路径范围。"""

    root = tmp_path / "allowed"
    target = root / "actual"
    target.mkdir(parents=True)
    (target / "notes.txt").write_text("research signal", encoding="utf-8")
    _create_symlink(root / "linked", target, is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        GrepRetriever(root).search("research", relative_path="linked")


def test_search_skips_symlinks_during_traversal(tmp_path: Path) -> None:
    """遍历时不得跟随或读取文件和目录符号链接。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "public.txt").write_text("research public", encoding="utf-8")
    linked_target = root / "linked-target.txt"
    linked_target.write_text("research linked", encoding="utf-8")
    _create_symlink(root / "linked.txt", linked_target)

    chunks = GrepRetriever(root).search("research")

    assert [chunk.source_ref for chunk in chunks] == ["linked-target.txt", "public.txt"]


def test_search_honors_file_size_and_file_count_limits(tmp_path: Path) -> None:
    """文件数和文件大小上限均在读取前生效。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "a.txt").write_text("research first", encoding="utf-8")
    (root / "b.txt").write_text("research second", encoding="utf-8")
    (root / "large.txt").write_text("research too large", encoding="utf-8")

    assert [chunk.source_ref for chunk in GrepRetriever(root, max_files=1).search("research")] == [
        "a.txt"
    ]
    assert GrepRetriever(root, max_file_bytes=5).search("research") == []


def test_search_skips_invalid_utf8_text(tmp_path: Path) -> None:
    """无法解码为 UTF-8 的文件不应返回任何文本证据。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "invalid.txt").write_bytes(b"research\xff")

    assert GrepRetriever(root).search("research") == []


@pytest.mark.parametrize(
    "content",
    [
        "research note\nAuthorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature\n",
        "research note\n7b2dJ0m9W1x3Q5r7T9v2Y4z6A8c0E2g4I6k8M0o\n",
    ],
    ids=["authorization-bearer", "unlabeled-long-secret"],
)
def test_search_skips_files_with_sensitive_value_patterns(
    tmp_path: Path, content: str
) -> None:
    """认证头和无标签长令牌所在文件不得产出任何证据。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.txt").write_text(content, encoding="utf-8")

    assert GrepRetriever(root).search("research") == []


def test_search_reads_at_most_one_byte_past_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件超过上限时，描述符读取请求不得超过上限加一个探测字节。"""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "oversized.txt").write_text("research", encoding="utf-8")
    read_sizes: list[int] = []
    real_read = os.read

    def record_read(fd: int, size: int) -> bytes:
        read_sizes.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", record_read)

    assert GrepRetriever(root, max_file_bytes=5).search("research") == []
    assert read_sizes
    assert sum(read_sizes) <= 6

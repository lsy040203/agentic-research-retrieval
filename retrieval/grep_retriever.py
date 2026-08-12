"""受可信目录边界约束的纯 Python 本地文本检索器。"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
import stat

from core.research_models import EvidenceChunk, ScopeKey


_SENSITIVE_FILENAME = re.compile(
    r"(?:^\.env(?:\.|$)|credential|secret|(?:api[_-]?)?key|token|password|passwd)",
    re.IGNORECASE,
)
_SENSITIVE_CONTENT = re.compile(
    r"(?:"
    r"(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|credential)\s*[:=]"
    r"|authorization\s*:\s*(?:bearer\s+)?[^\s,;]+"
    r"|bearer\s+[A-Za-z0-9._~+/=-]+"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|(?<![A-Za-z0-9])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9])"
    r")",
    re.IGNORECASE,
)


class GrepRetriever:
    """在受信任根目录内逐行检索非敏感的本地文本证据。"""

    def __init__(
        self,
        root: Path,
        *,
        max_results: int = 20,
        max_files: int = 200,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        """解析可信根目录并保存不可突破的读取上限。"""

        if max_results < 1 or max_files < 1 or max_file_bytes < 1:
            raise ValueError("search limits must be positive")

        resolved_root = Path(root).resolve(strict=True)
        if not resolved_root.is_dir():
            raise ValueError("root must be an existing directory")

        self._root = resolved_root
        self._max_results = max_results
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._scope = ScopeKey(
            team_id="local",
            project_id="local",
            repository=resolved_root.as_posix(),
            branch="workspace",
            experiment_environment="local",
        )

    def search(self, query: str, *, relative_path: str = ".") -> list[EvidenceChunk]:
        """返回在允许相对路径范围内大小写无关匹配到的行级证据。"""

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be blank")

        start = self._resolve_search_path(relative_path)
        if not start.exists():
            return []

        results: list[EvidenceChunk] = []
        files_scanned = 0
        for path in self._iter_files(start):
            if files_scanned >= self._max_files or len(results) >= self._max_results:
                break
            files_scanned += 1

            if self._is_sensitive_path(path):
                continue

            data = self._read_safe_file(path)
            if data is None:
                continue

            # NUL 是可靠的二进制信号；二进制内容绝不进入证据或元数据。
            if b"\x00" in data:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            # 整个文件命中密钥赋值模式时跳过，避免返回同文件其他行造成泄露。
            if _SENSITIVE_CONTENT.search(text):
                continue

            for line_number, line in enumerate(text.splitlines(), start=1):
                if normalized_query.casefold() not in line.casefold():
                    continue
                results.append(self._to_evidence(path, line, line_number, normalized_query))
                if len(results) >= self._max_results:
                    break

        return results

    def _read_safe_file(self, path: Path) -> bytes | None:
        """通过已核验的文件描述符读取受限字节，避免检查与读取间的替换。"""

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is not None:
            flags |= nofollow

        try:
            file_descriptor = os.open(path, flags)
        except OSError:
            return None

        try:
            descriptor_stat = os.fstat(file_descriptor)
            # 仅接受普通文件；目录、设备和其他特殊文件都不得作为证据读取。
            if not stat.S_ISREG(descriptor_stat.st_mode):
                return None
            if not self._descriptor_matches_trusted_path(path, descriptor_stat):
                return None

            # 只请求上限加一个字节，以一个探测字节可靠识别超限或读取期增长。
            data = os.read(file_descriptor, self._max_file_bytes + 1)
            if len(data) > self._max_file_bytes:
                return None
            return data
        except OSError:
            return None
        finally:
            os.close(file_descriptor)

    def _descriptor_matches_trusted_path(self, path: Path, descriptor_stat: os.stat_result) -> bool:
        """确认已打开描述符仍对应根目录内、非链接的同一个普通文件。"""

        try:
            if path.is_symlink():
                return False
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(self._root)
            # 无 O_NOFOLLOW 时必须做身份绑定；不能验证身份就拒绝读取。
            current_stat = path.stat()
            if not os.path.samestat(descriptor_stat, current_stat):
                return False
            if not getattr(os, "O_NOFOLLOW", None):
                # 此处保证打开后的句柄与当前根内路径是同一文件，作为安全回退。
                return resolved_path == path.absolute()
            return True
        except OSError:
            return False
        except ValueError:
            return False

    def _resolve_search_path(self, relative_path: str) -> Path:
        """解析请求范围，并拒绝绝对路径或任何根目录逃逸。"""

        requested = Path(relative_path)
        if requested.is_absolute():
            raise ValueError("relative_path must be relative")

        unresolved_component = self._root
        for component in requested.parts:
            if component == ".":
                continue
            unresolved_component = unresolved_component / component
            # 在 resolve 前检查每个原始路径分量，防止根内链接绕过边界策略。
            if unresolved_component.is_symlink():
                raise ValueError("relative_path must not contain a symlink")

        candidate = (self._root / requested).resolve()
        # 即使 ``..`` 被规范化，也必须仍留在受信任根目录中。
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("relative_path escapes root") from error
        return candidate

    def _iter_files(self, start: Path):
        """深度优先地枚举根目录内的普通文件，不跟随符号链接。"""

        if start.is_symlink():
            return
        if start.is_file():
            yield start
            return
        if not start.is_dir():
            return

        try:
            children = sorted(start.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            return
        for child in children:
            # 符号链接可能在检查后指向根外，因此一律不作为可检索对象。
            if child.is_symlink():
                continue
            if child.is_file():
                yield child
            elif child.is_dir():
                yield from self._iter_files(child)

    def _is_sensitive_path(self, path: Path) -> bool:
        """判断根目录下任一路径分量是否明显表示凭据或密钥内容。"""

        try:
            relative_parts = path.relative_to(self._root).parts
        except ValueError:
            # 无法证明来源仍在可信根目录内时，保守拒绝读取。
            return True
        return any(_SENSITIVE_FILENAME.search(part) is not None for part in relative_parts)

    def _to_evidence(
        self, path: Path, line: str, line_number: int, query: str
    ) -> EvidenceChunk:
        """将已通过安全检查的文本行转换为可追溯证据。"""

        source_ref = path.relative_to(self._root).as_posix()
        chunk_id = sha256(f"{source_ref}:{line_number}".encode("utf-8")).hexdigest()
        return EvidenceChunk(
            chunk_id=f"python-grep:{chunk_id}",
            scope=self._scope,
            content=line,
            source_ref=source_ref,
            locator=f"line:{line_number}",
            metadata={"retriever": "python-grep", "query": query},
        )

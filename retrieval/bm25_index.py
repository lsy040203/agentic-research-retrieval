"""ARR 的标准库、按 Scope 隔离的 BM25 持久化索引。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable

from core.research_models import EvidenceChunk, ScopeKey


_SCHEMA = "arr.bm25_index"
_VERSION = 1
_K1 = 1.5
_B = 0.75
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


class BM25IndexError(ValueError):
    """索引不可读取或不满足安全契约时使用的受控异常。"""


@dataclass(frozen=True)
class _LoadedIndex:
    """经校验后的索引内容，避免将未校验 JSON 传播到检索阶段。"""

    scope: ScopeKey
    built_at: datetime
    documents: dict[str, EvidenceChunk]
    postings: dict[str, list[tuple[str, int]]]
    document_count: int
    avg_doc_length: float


def tokenize(value: str) -> list[str]:
    """以大小写无关的纯 Python 规则拆分索引和查询文本。"""

    if not isinstance(value, str):
        return []
    return [token.casefold() for token in _TOKEN_PATTERN.findall(value)]


class BM25Index:
    """构建、原子保存并查询单个 ScopeKey 的 BM25 倒排索引。"""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def index_path(self, scope: ScopeKey) -> Path:
        """返回 Scope 的确定性且文件名安全的索引位置。"""

        if not isinstance(scope, ScopeKey):
            raise TypeError("scope must be a ScopeKey")
        # 完整五维范围参与编码，防止仅靠部分字段造成跨范围碰撞。
        encoded = "--".join(
            value.encode("utf-8").hex()
            for value in (
                scope.team_id,
                scope.project_id,
                scope.repository,
                scope.branch,
                scope.experiment_environment,
            )
        )
        return self._directory / f"bm25-{encoded}.json"

    def build(self, scope: ScopeKey, chunks: Iterable[EvidenceChunk]) -> None:
        """以给定证据重建一个范围索引，并通过同目录临时文件原子替换。"""

        if not isinstance(scope, ScopeKey):
            raise TypeError("scope must be a ScopeKey")
        evidence = list(chunks)
        if any(not isinstance(chunk, EvidenceChunk) for chunk in evidence):
            raise TypeError("chunks must contain EvidenceChunk instances")
        if any(chunk.scope != scope for chunk in evidence):
            raise ValueError("all chunks must belong to scope")

        # 先按 ID 排序，令文档和 posting 的序列化顺序可重复。
        ordered = sorted(evidence, key=lambda chunk: chunk.chunk_id)
        if len({chunk.chunk_id for chunk in ordered}) != len(ordered):
            raise ValueError("chunk_id must be unique within an index")

        documents: list[dict[str, Any]] = []
        postings: dict[str, list[dict[str, int | str]]] = {}
        lengths: list[int] = []
        for chunk in ordered:
            document = chunk.to_dict()
            documents.append(document)
            counts: dict[str, int] = {}
            for term in tokenize(chunk.content):
                counts[term] = counts.get(term, 0) + 1
            lengths.append(sum(counts.values()))
            for term in sorted(counts):
                postings.setdefault(term, []).append(
                    {"chunk_id": chunk.chunk_id, "term_frequency": counts[term]}
                )

        document_count = len(documents)
        avg_doc_length = sum(lengths) / document_count if document_count else 0.0
        payload = {
            "schema": _SCHEMA,
            "version": _VERSION,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "scope": self._scope_dict(scope),
            "k1": _K1,
            "b": _B,
            "document_count": document_count,
            "avg_doc_length": avg_doc_length,
            "documents": documents,
            "postings": {term: postings[term] for term in sorted(postings)},
        }
        self._write_atomic(self.index_path(scope), payload)

    def load(self, scope: ScopeKey) -> _LoadedIndex:
        """加载并严格校验一个范围的索引；异常时不产生部分结果。"""

        path = self.index_path(scope)
        try:
            with path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BM25IndexError("BM25 index is unavailable") from error
        return self._parse_payload(payload, scope)

    def query(self, scope: ScopeKey, query: str) -> list[tuple[EvidenceChunk, float]]:
        """按照标准 BM25 分数和固定次级键返回范围内的证据副本。"""

        return self.query_loaded(self.load(scope), query)

    def query_loaded(
        self, loaded: _LoadedIndex, query: str
    ) -> list[tuple[EvidenceChunk, float]]:
        """只对已校验快照评分，避免检索期间第二次读取已被替换的索引。"""

        if not isinstance(loaded, _LoadedIndex):
            raise TypeError("loaded must be a validated BM25 index")
        query_terms = tokenize(query)
        if not query_terms or not loaded.document_count:
            return []

        query_counts: dict[str, int] = {}
        for term in query_terms:
            query_counts[term] = query_counts.get(term, 0) + 1
        scores: dict[str, float] = {}
        lengths = {chunk_id: len(tokenize(chunk.content)) for chunk_id, chunk in loaded.documents.items()}
        average_length = loaded.avg_doc_length if loaded.avg_doc_length > 0 else 1.0
        for term in query_counts:
            posting_list = loaded.postings.get(term, [])
            if not posting_list:
                continue
            document_frequency = len(posting_list)
            # 非负稳定 IDF，避免高频词带来负分数。
            idf = math.log(1 + (loaded.document_count - document_frequency + 0.5) / (document_frequency + 0.5))
            for chunk_id, frequency in posting_list:
                length = lengths[chunk_id]
                denominator = frequency + _K1 * (1 - _B + _B * length / average_length)
                contribution = idf * (frequency * (_K1 + 1)) / denominator
                scores[chunk_id] = scores.get(chunk_id, 0.0) + contribution

        ranked = [(loaded.documents[chunk_id], score) for chunk_id, score in scores.items() if math.isfinite(score) and score >= 0]
        return sorted(
            ranked,
            key=lambda item: (-item[1], item[0].source_ref, item[0].locator or "", item[0].chunk_id),
        )

    def latest_built_at(self) -> datetime | None:
        """返回目录内任一有效索引的最新构建时间，供路由器预检新鲜度。"""

        if not self._directory.is_dir():
            return None
        latest: datetime | None = None
        for path in self._directory.glob("bm25-*.json"):
            try:
                with path.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                if payload.get("schema") != _SCHEMA or payload.get("version") != _VERSION:
                    continue
                built_at = self._parse_time(payload.get("built_at"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, BM25IndexError, AttributeError):
                continue
            if latest is None or built_at > latest:
                latest = built_at
        return latest

    @staticmethod
    def _scope_dict(scope: ScopeKey) -> dict[str, str]:
        """将 ScopeKey 还原为 JSON 中完整的五个字段。"""

        return {
            "team_id": scope.team_id,
            "project_id": scope.project_id,
            "repository": scope.repository,
            "branch": scope.branch,
            "experiment_environment": scope.experiment_environment,
        }

    def _parse_payload(self, payload: Any, expected_scope: ScopeKey) -> _LoadedIndex:
        """校验持久化格式并恢复完整 EvidenceChunk，防止跨 Scope 读取。"""

        if not isinstance(payload, dict):
            raise BM25IndexError("BM25 payload must be an object")
        if payload.get("schema") != _SCHEMA or payload.get("version") != _VERSION:
            raise BM25IndexError("BM25 schema or version is incompatible")
        if payload.get("scope") != self._scope_dict(expected_scope):
            raise BM25IndexError("BM25 scope does not match")
        if payload.get("k1") != _K1 or payload.get("b") != _B:
            raise BM25IndexError("BM25 parameters are incompatible")
        built_at = self._parse_time(payload.get("built_at"))
        documents_payload = payload.get("documents")
        postings_payload = payload.get("postings")
        document_count = payload.get("document_count")
        avg_doc_length = payload.get("avg_doc_length")
        if not isinstance(documents_payload, list) or not isinstance(postings_payload, dict):
            raise BM25IndexError("BM25 documents or postings are invalid")
        if isinstance(document_count, bool) or not isinstance(document_count, int) or document_count != len(documents_payload):
            raise BM25IndexError("BM25 document count is invalid")
        if isinstance(avg_doc_length, bool) or not isinstance(avg_doc_length, (int, float)) or not math.isfinite(avg_doc_length) or avg_doc_length < 0:
            raise BM25IndexError("BM25 average document length is invalid")

        documents: dict[str, EvidenceChunk] = {}
        for document in documents_payload:
            chunk = self._chunk_from_dict(document, expected_scope)
            if chunk.chunk_id in documents:
                raise BM25IndexError("BM25 document identifiers are not unique")
            documents[chunk.chunk_id] = chunk

        # 倒排表、词频和平均长度都是正文的派生数据，不能信任持久化副本。
        expected_postings, expected_avg_length = self._derived_postings(documents)
        if float(avg_doc_length) != expected_avg_length:
            raise BM25IndexError("BM25 average document length does not match documents")
        postings: dict[str, list[tuple[str, int]]] = {}
        for term, entries in postings_payload.items():
            if not isinstance(term, str) or not isinstance(entries, list):
                raise BM25IndexError("BM25 posting is invalid")
            normalized_entries: list[tuple[str, int]] = []
            # 每个 term 仅维护一份已见集合，避免高频 posting 退化为 O(n²)。
            seen_chunk_ids: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise BM25IndexError("BM25 posting entry is invalid")
                chunk_id, frequency = entry.get("chunk_id"), entry.get("term_frequency")
                if chunk_id not in documents or isinstance(frequency, bool) or not isinstance(frequency, int) or frequency <= 0:
                    raise BM25IndexError("BM25 posting reference is invalid")
                if chunk_id in seen_chunk_ids:
                    raise BM25IndexError("BM25 posting contains a duplicate document")
                seen_chunk_ids.add(chunk_id)
                normalized_entries.append((chunk_id, frequency))
            postings[term] = normalized_entries
        if postings != expected_postings:
            raise BM25IndexError("BM25 postings do not match documents")
        return _LoadedIndex(expected_scope, built_at, documents, postings, document_count, float(avg_doc_length))

    @staticmethod
    def _derived_postings(
        documents: dict[str, EvidenceChunk],
    ) -> tuple[dict[str, list[tuple[str, int]]], float]:
        """从已恢复的正文重建 canonical posting 和文档长度统计。"""

        postings: dict[str, list[tuple[str, int]]] = {}
        document_lengths: list[int] = []
        for chunk_id in sorted(documents):
            counts: dict[str, int] = {}
            for term in tokenize(documents[chunk_id].content):
                counts[term] = counts.get(term, 0) + 1
            document_lengths.append(sum(counts.values()))
            for term in sorted(counts):
                postings.setdefault(term, []).append((chunk_id, counts[term]))
        average = sum(document_lengths) / len(document_lengths) if document_lengths else 0.0
        return postings, average

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        """将 ISO 时间规范化为带 UTC 时区的 datetime。"""

        if not isinstance(value, str):
            raise BM25IndexError("BM25 built_at is invalid")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise BM25IndexError("BM25 built_at is invalid") from error
        if parsed.tzinfo is None:
            raise BM25IndexError("BM25 built_at must include timezone")
        return parsed.astimezone(timezone.utc)

    def _chunk_from_dict(self, value: Any, expected_scope: ScopeKey) -> EvidenceChunk:
        """恢复索引中完整的 EvidenceChunk 必需字段。"""

        if not isinstance(value, dict) or value.get("scope") != self._scope_dict(expected_scope):
            raise BM25IndexError("BM25 document scope is invalid")
        try:
            # EvidenceChunk 的既有默认时间可能是无时区 datetime；保真恢复即可。
            created_at = self._parse_evidence_time(value["created_at"])
            return EvidenceChunk(
                chunk_id=value["chunk_id"], scope=expected_scope, content=value["content"],
                source_ref=value["source_ref"], locator=value.get("locator"),
                vector_score=value.get("vector_score"), rerank_score=value.get("rerank_score"),
                rerank_reason=value.get("rerank_reason"), metadata=dict(value.get("metadata", {})),
                created_at=created_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BM25IndexError("BM25 document is invalid") from error

    @staticmethod
    def _parse_evidence_time(value: Any) -> datetime:
        """恢复 EvidenceChunk 的 ISO 时间，兼容领域模型已有的无时区默认值。"""

        if not isinstance(value, str):
            raise BM25IndexError("BM25 evidence created_at is invalid")
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise BM25IndexError("BM25 evidence created_at is invalid") from error

    @staticmethod
    def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
        """先将 JSON 写到同目录临时文件，再使用 replace 原子发布。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
            ) as temporary:
                temporary_name = temporary.name
                json.dump(payload, temporary, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
                temporary.flush()
            Path(temporary_name).replace(path)
        except (OSError, TypeError, ValueError) as error:
            raise BM25IndexError("BM25 index could not be saved") from error
        finally:
            if temporary_name is not None:
                temporary_path = Path(temporary_name)
                if temporary_path.exists():
                    try:
                        temporary_path.unlink()
                    except OSError:
                        pass

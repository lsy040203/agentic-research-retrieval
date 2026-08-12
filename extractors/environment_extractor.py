from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

from core.constants import MemoryType, Scene
from core.models import MemoryCandidate


_SENSITIVE_TOKENS = ("password", "passwd", "secret", "token", "api_key", "access_key", "credential", "authorization")
_DOWNLOAD_KEYS = {"downloads", "download", "download_dir", "downloads_dir", "download_path"}
_DOCUMENT_KEYS = {"documents", "document", "docs", "doc", "documents_dir", "document_dir", "docs_dir", "doc_dir", "documents_path", "docs_path"}
_LANGUAGE_KEYS = {"language", "lang", "system_language", "ui_language", "display_language"}
_REGION_KEYS = {"region", "country", "locale_region", "system_region"}
_LOCALE_KEYS = {"locale", "system_locale", "language_locale"}
_SOFTWARE_KEYS = {"installed_software", "installed_applications", "applications", "software", "packages", "installed_packages"}
_VERSION_KEYS = {"os_version", "system_version", "operating_system", "platform", "os", "system"}


def _slugify(value: str) -> str:
    token = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", value.strip().lower())
    return token.strip("_") or "environment"


def _stable_candidate_id(user_id: str, key: str) -> str:
    payload = f"{user_id}\x1f{MemoryType.ENVIRONMENT.value}\x1f{key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _walk(payload: Any, path: tuple[str, ...] = (), visited: set[int] | None = None) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Yield nested values without recursing forever on malformed cyclic input."""
    visited = visited if visited is not None else set()
    if isinstance(payload, dict):
        marker = id(payload)
        if marker in visited:
            return
        visited.add(marker)
        for raw_key, value in payload.items():
            key = str(raw_key).strip().lower()
            if any(token in key for token in _SENSITIVE_TOKENS):
                continue
            yield from _walk(value, path + (key,), visited)
        visited.remove(marker)
        return
    if isinstance(payload, (list, tuple, set)):
        marker = id(payload)
        if marker in visited:
            return
        visited.add(marker)
        values = sorted(payload, key=str) if isinstance(payload, set) else payload
        for index, value in enumerate(values):
            yield from _walk(value, path + (str(index),), visited)
        visited.remove(marker)
        return
    yield path, payload


def _leaf_key(path: tuple[str, ...]) -> str:
    return path[-1] if path else ""


def _sanitise_path(value: str, directory: str) -> str:
    raw_path = value.strip()
    path = raw_path.replace("/", "\\")
    windows_home = re.compile(r"^[a-zA-Z]:\\users\\[^\\]+(?P<tail>\\.*)$", re.IGNORECASE)
    unix_home = re.compile(r"^/(?:home|users)/[^/]+(?P<tail>/.*)$", re.IGNORECASE)
    match = windows_home.match(path)
    if match:
        path = "~" + match.group("tail")
    else:
        unix_match = unix_home.match(raw_path)
        if unix_match:
            path = "~" + unix_match.group("tail").replace("/", "\\")
    path_tail = path.rstrip("\\").rsplit("\\", 1)[-1].lower()
    expected_tails = {
        "downloads": {"downloads", "download"},
        "documents": {"documents", "document", "docs", "doc"},
    }.get(directory.lower(), {directory.lower()})
    if path_tail not in expected_tails:
        if re.match(r"^(?:[A-Za-z]:\\|\\\\|\\|~\\)", path) or raw_path.startswith(("/", "~")):
            return path
        return directory
    return path


def _path_category(path: tuple[str, ...], value: Any) -> str | None:
    key = _leaf_key(path)
    if key in _DOWNLOAD_KEYS:
        return "downloads"
    if key in _DOCUMENT_KEYS:
        return "documents"
    if isinstance(value, str):
        lowered = value.replace("/", "\\").rstrip("\\").lower()
        if lowered.endswith("\\downloads"):
            return "downloads"
        if lowered.endswith("\\documents") or lowered.endswith("\\docs"):
            return "documents"
    return None


def _parse_locale(value: str) -> tuple[str | None, str | None]:
    normalized = value.strip().replace("-", "_").split(".", 1)[0]
    parts = [part for part in normalized.split("_") if part]
    if not parts:
        return None, None
    language = parts[0].lower()
    region = parts[1].upper() if len(parts) > 1 and len(parts[1]) in {2, 3} else None
    return language, region


def _software_entries(value: Any) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    if isinstance(value, str):
        for item in re.split(r"[,;\n]", value):
            name = item.strip()
            if name:
                entries.append((name, None))
    elif isinstance(value, dict):
        if any(key in value for key in ("name", "display_name", "package")):
            name = value.get("name") or value.get("display_name") or value.get("package")
            version = value.get("version") or value.get("release")
            if isinstance(name, str) and name.strip():
                entries.append((name.strip(), str(version).strip() if version is not None else None))
        else:
            for name, version in value.items():
                if isinstance(version, dict):
                    nested_version = version.get("version") or version.get("release")
                    entries.append((str(name), str(nested_version).strip() if nested_version is not None else None))
                elif version is not None:
                    entries.append((str(name), str(version).strip()))
    elif isinstance(value, (list, tuple, set)):
        values = sorted(value, key=str) if isinstance(value, set) else value
        for item in values:
            entries.extend(_software_entries(item))
    return [(name, version) for name, version in entries if name.strip()]


def _make_candidate(
    *,
    user_id: str,
    key: str,
    content: str,
    confidence: float,
    metadata: dict[str, Any],
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=_stable_candidate_id(user_id, key),
        user_id=user_id,
        memory_type=MemoryType.ENVIRONMENT,
        key=key,
        content=content,
        scenario=Scene.SYSTEM,
        confidence=confidence,
        source="environment_tool_output",
        tags=["environment", metadata["category"]],
        metadata=metadata,
    )


class EnvironmentExtractor:
    @staticmethod
    def extract_from_tool_output(output: dict) -> list[MemoryCandidate]:
        """Extract normalised, non-sensitive environment facts from tool output."""
        if not isinstance(output, dict):
            raise TypeError("output must be a dict")

        user_id = str(output.get("user_id", "")).strip()
        candidates: dict[str, MemoryCandidate] = {}

        def add(candidate: MemoryCandidate) -> None:
            existing = candidates.get(candidate.key)
            if existing is None or candidate.confidence > existing.confidence:
                candidates[candidate.key] = candidate

        for path, value in _walk(output):
            key = _leaf_key(path)
            if value is None:
                continue

            directory = _path_category(path, value)
            if directory and isinstance(value, str) and value.strip():
                normalised_path = _sanitise_path(value, directory)
                add(
                    _make_candidate(
                        user_id=user_id,
                        key=f"environment.path.{directory}",
                        content=f"常用目录 {directory}: {normalised_path}",
                        confidence=0.95,
                        metadata={"category": "path", "directory": directory, "path": normalised_path, "source_key": ".".join(path)},
                    )
                )

            if key in (_LANGUAGE_KEYS | _LOCALE_KEYS) and isinstance(value, str) and value.strip():
                language, inferred_region = _parse_locale(value)
                if language:
                    add(
                        _make_candidate(
                            user_id=user_id,
                            key="environment.locale.language",
                            content=f"系统语言: {language}",
                            confidence=0.9,
                            metadata={"category": "locale", "language": language, "source_key": ".".join(path)},
                        )
                    )
                if inferred_region:
                    add(
                        _make_candidate(
                            user_id=user_id,
                            key="environment.locale.region",
                            content=f"系统地区: {inferred_region}",
                            confidence=0.82,
                            metadata={"category": "locale", "region": inferred_region, "source_key": ".".join(path)},
                        )
                    )

            if key in _REGION_KEYS and isinstance(value, str) and value.strip():
                region = value.strip().upper()
                add(
                    _make_candidate(
                        user_id=user_id,
                        key="environment.locale.region",
                        content=f"系统地区: {region}",
                        confidence=0.9,
                        metadata={"category": "locale", "region": region, "source_key": ".".join(path)},
                    )
                )

        # Software collections need to retain their container shape, so inspect
        # top-level and nested dictionaries separately from scalar walking.
        containers: list[tuple[tuple[str, ...], Any]] = []

        def collect_containers(payload: Any, path: tuple[str, ...] = (), visited: set[int] | None = None) -> None:
            visited = visited if visited is not None else set()
            if not isinstance(payload, dict):
                return
            marker = id(payload)
            if marker in visited:
                return
            visited.add(marker)
            for raw_key, value in payload.items():
                key = str(raw_key).strip().lower()
                child_path = path + (key,)
                if any(token in key for token in _SENSITIVE_TOKENS):
                    continue
                if key in _SOFTWARE_KEYS:
                    containers.append((child_path, value))
                if isinstance(value, dict):
                    collect_containers(value, child_path, visited)
            visited.remove(marker)

        collect_containers(output)
        for path, value in containers:
            for name, version in _software_entries(value):
                software_key = f"environment.software.{_slugify(name)}"
                display = f"已安装软件: {name}" + (f" {version}" if version else "")
                add(
                    _make_candidate(
                        user_id=user_id,
                        key=software_key,
                        content=display,
                        confidence=0.9,
                        metadata={"category": "software", "name": name, "version": version, "source_key": ".".join(path)},
                    )
                )

        version_values: list[tuple[tuple[str, ...], str]] = []

        def collect_versions(payload: Any, path: tuple[str, ...] = (), visited: set[int] | None = None) -> None:
            visited = visited if visited is not None else set()
            if not isinstance(payload, dict):
                return
            marker = id(payload)
            if marker in visited:
                return
            visited.add(marker)
            for raw_key, value in payload.items():
                key = str(raw_key).strip().lower()
                child_path = path + (key,)
                if any(token in key for token in _SENSITIVE_TOKENS):
                    continue
                if key in _VERSION_KEYS:
                    if isinstance(value, str) and value.strip():
                        version_values.append((child_path, value.strip()))
                    elif isinstance(value, dict):
                        name = value.get("name") or value.get("os_name") or value.get("distribution")
                        version = value.get("version") or value.get("release") or value.get("build")
                        rendered = " ".join(str(item).strip() for item in (name, version) if item is not None and str(item).strip())
                        if rendered:
                            version_values.append((child_path, rendered))
                if isinstance(value, dict):
                    collect_versions(value, child_path, visited)
            visited.remove(marker)

        collect_versions(output)
        if version_values:
            path, version = sorted(version_values, key=lambda item: (len(item[0]), item[0]))[0]
            add(
                _make_candidate(
                    user_id=user_id,
                    key="environment.system.version",
                    content=f"系统版本: {version}",
                    confidence=0.9,
                    metadata={"category": "system", "version": version, "source_key": ".".join(path)},
                )
            )

        return [candidates[key] for key in sorted(candidates)]

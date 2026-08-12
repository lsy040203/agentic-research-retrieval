from __future__ import annotations

from core.constants import MemoryType
from extractors.environment_extractor import EnvironmentExtractor


def test_extract_environment_from_structured_tool_output() -> None:
    output = {
        "user_id": "alice",
        "paths": {
            "Downloads": r"C:\Users\alice\Downloads",
            "Documents": r"C:\Users\alice\Documents",
        },
        "locale": "zh_CN.UTF-8",
        "installed_software": [
            {"name": "LibreOffice", "version": "24.2"},
            {"display_name": "Firefox", "release": "126"},
        ],
        "system": {"os_name": "Kylin Desktop", "version": "V11"},
        "api_key": "must-not-be-extracted",
    }

    candidates = EnvironmentExtractor.extract_from_tool_output(output)
    by_key = {candidate.key: candidate for candidate in candidates}

    assert set(by_key) >= {
        "environment.path.downloads",
        "environment.path.documents",
        "environment.locale.language",
        "environment.locale.region",
        "environment.software.libreoffice",
        "environment.software.firefox",
        "environment.system.version",
    }
    assert all(candidate.user_id == "alice" for candidate in candidates)
    assert all(candidate.memory_type is MemoryType.ENVIRONMENT for candidate in candidates)
    assert by_key["environment.path.downloads"].metadata["path"] == r"~\Downloads"
    assert by_key["environment.path.documents"].metadata["path"] == r"~\Documents"
    assert by_key["environment.locale.language"].metadata["language"] == "zh"
    assert by_key["environment.locale.region"].metadata["region"] == "CN"
    assert by_key["environment.software.libreoffice"].metadata["version"] == "24.2"
    assert by_key["environment.system.version"].metadata["version"] == "Kylin Desktop V11"
    assert "api_key" not in str([candidate.to_dict() for candidate in candidates])


def test_environment_extraction_supports_common_output_shapes_and_is_deterministic() -> None:
    output = {
        "downloads": "/home/bob/Downloads",
        "documents": "/home/bob/Documents",
        "language": "en-US",
        "region": "us",
        "applications": {
            "VS Code": "1.90",
            "git": {"version": "2.45"},
        },
        "os_version": "Ubuntu 24.04",
    }

    first = EnvironmentExtractor.extract_from_tool_output(output)
    second = EnvironmentExtractor.extract_from_tool_output(output)
    by_key = {candidate.key: candidate for candidate in first}

    assert by_key["environment.path.downloads"].metadata["path"] == r"~\Downloads"
    assert by_key["environment.path.documents"].metadata["path"] == r"~\Documents"
    assert by_key["environment.locale.language"].metadata["language"] == "en"
    assert by_key["environment.locale.region"].metadata["region"] == "US"
    assert by_key["environment.software.vs_code"].metadata["version"] == "1.90"
    assert by_key["environment.software.git"].metadata["version"] == "2.45"
    assert by_key["environment.system.version"].metadata["version"] == "Ubuntu 24.04"
    assert [(candidate.key, candidate.candidate_id) for candidate in first] == [
        (candidate.key, candidate.candidate_id) for candidate in second
    ]


def test_environment_extractor_rejects_non_mapping_output() -> None:
    try:
        EnvironmentExtractor.extract_from_tool_output([])  # type: ignore[arg-type]
    except TypeError as exc:
        assert "output must be a dict" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("non-mapping output must be rejected")


def test_environment_preserves_absolute_docs_path_and_filters_nested_secrets() -> None:
    output = {
        "docs": "/root/docs",
        "nested": {
            "token": "must-not-leak",
            "paths": {"download_dir": "/root/downloads"},
        },
    }

    candidates = EnvironmentExtractor.extract_from_tool_output(output)
    by_key = {candidate.key: candidate for candidate in candidates}
    rendered = str([candidate.to_dict() for candidate in candidates])

    assert by_key["environment.path.documents"].metadata["path"] == r"\root\docs"
    assert by_key["environment.path.downloads"].metadata["path"] == r"\root\downloads"
    assert "must-not-leak" not in rendered

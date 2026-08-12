"""Explicit manual entry point for bounded, redacted live LLM evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.live_llm_eval import (  # noqa: E402
    LiveCallBudget,
    LiveCaseResult,
    LiveEvalCase,
    LiveEvaluationReport,
    LiveEvaluationValidationError,
    load_live_eval_dataset,
    run_live_case,
)
from policy.llm_prompts import ROUTER_PROMPT_VERSION  # noqa: E402
from policy.llm_query_planner import LLMQueryPlanner  # noqa: E402
from retrieval.llm_reranker import (  # noqa: E402
    LocalLLMReranker,
    LocalLLMSettings,
    load_llm_settings_from_environment,
)


_MODES = frozenset({"rule", "llm", "hybrid"})
_GOLD_DATASET = Path("data/gold/llm_live_eval.json")
_REPORT_DIRECTORY = Path("data/eval_reports")


class LiveEvalCommandError(ValueError):
    """Raised for invalid manual-evaluation input without exposing sensitive data."""

    def __init__(self, message: str, *, source: str = "configuration") -> None:
        super().__init__(message)
        self.source = source


_REDACTED_ERROR_CODES = {
    "configuration": "configuration_rejected",
    "dataset": "dataset_rejected",
    "report_path": "report_path_rejected",
    "unsafe_scope": "unsafe_scope_rejected",
}


class _RedactedArgumentParser(argparse.ArgumentParser):
    """Classify parser failures without allowing argparse to echo user input."""

    def error(self, message: str) -> None:
        del message
        raise LiveEvalCommandError("invalid command arguments", source="configuration")


Runner = Callable[..., Sequence[tuple[LiveEvalCase, LiveCaseResult]]]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the small, explicit command surface without reading the environment."""
    parser = _RedactedArgumentParser(description="Run a bounded manual live LLM evaluation.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--mode", choices=sorted(_MODES), default="hybrid")
    parser.add_argument("--max-live-calls", default="20")
    args = parser.parse_args(argv)
    try:
        max_live_calls = int(args.max_live_calls)
    except (TypeError, ValueError) as error:
        raise LiveEvalCommandError("invalid live call limit", source="configuration") from error
    if not 1 <= max_live_calls <= 20:
        raise LiveEvalCommandError("invalid live call limit", source="configuration")
    args.max_live_calls = max_live_calls
    return args


def load_manual_llm_settings(environ: Mapping[str, str]) -> LocalLLMSettings:
    """Load and require the existing environment-backed, endpoint-restricted settings."""
    settings = load_llm_settings_from_environment(environ)
    if LocalLLMReranker._settings_degradation_reason(settings) is not None:
        raise LiveEvalCommandError(
            "manual live LLM settings are incomplete or unsafe", source="configuration"
        )
    return settings


def resolve_live_eval_dataset(dataset_path: Path) -> Path:
    """Accept only the project's ordinary, approved local gold fixture."""
    if dataset_path.is_absolute() or ".." in dataset_path.parts:
        raise LiveEvalCommandError("manual evaluation dataset is invalid", source="dataset")
    if dataset_path != _GOLD_DATASET:
        raise LiveEvalCommandError("manual evaluation dataset is invalid", source="dataset")
    candidate = PROJECT_ROOT / dataset_path
    current = PROJECT_ROOT
    for part in dataset_path.parts:
        current /= part
        if current.is_symlink():
            raise LiveEvalCommandError("manual evaluation dataset is invalid", source="dataset")
    try:
        resolved = candidate.resolve(strict=True)
        expected = (PROJECT_ROOT / _GOLD_DATASET).resolve(strict=True)
    except OSError as error:
        raise LiveEvalCommandError("manual evaluation dataset is invalid", source="dataset") from error
    if resolved != expected or not candidate.is_file():
        raise LiveEvalCommandError("manual evaluation dataset is invalid", source="dataset")
    return resolved


def _safe_report_directory() -> Path:
    """Create the fixed report directory without following a symlinked parent."""
    try:
        if PROJECT_ROOT.is_symlink():
            raise LiveEvalCommandError("manual evaluation report path is unsafe", source="report_path")
        project_root = PROJECT_ROOT.resolve(strict=True)
    except OSError as error:
        raise LiveEvalCommandError("manual evaluation report path is unsafe", source="report_path") from error
    current = PROJECT_ROOT
    for part in _REPORT_DIRECTORY.parts:
        current /= part
        try:
            current.mkdir(exist_ok=True)
            if current.is_symlink() or not current.is_dir():
                raise LiveEvalCommandError("manual evaluation report path is unsafe", source="report_path")
            resolved = current.resolve(strict=True)
        except OSError as error:
            raise LiveEvalCommandError("manual evaluation report path is unsafe", source="report_path") from error
        if not resolved.is_relative_to(project_root):
            raise LiveEvalCommandError("manual evaluation report path is unsafe", source="report_path")
    return current


def _create_report(output_dir: Path, filename_stem: str, serialized: str) -> Path:
    """Use exclusive creation so a collision can never overwrite a prior report."""
    sequence = 0
    while sequence < 1000:
        suffix = "" if sequence == 0 else f"_{sequence}"
        output_path = output_dir / f"{filename_stem}{suffix}.json"
        _safe_report_directory()
        try:
            with output_path.open("x", encoding="utf-8") as report_file:
                report_file.write(serialized)
            return output_path
        except FileExistsError:
            sequence += 1
        except OSError as error:
            raise LiveEvalCommandError(
                "manual evaluation report path is unavailable", source="report_path"
            ) from error
    raise LiveEvalCommandError("manual evaluation report path is unavailable", source="report_path")


def _default_runner(
    cases: Sequence[LiveEvalCase],
    *,
    mode: str,
    budget: LiveCallBudget,
    planner: LLMQueryPlanner,
    reranker: LocalLLMReranker,
) -> list[tuple[LiveEvalCase, LiveCaseResult]]:
    return [
        (
            case,
            run_live_case(
                case,
                mode=mode,  # type: ignore[arg-type]
                budget=budget,
                planner=planner,
                reranker=reranker,
            ),
        )
        for case in cases
    ]


def run_manual_live_evaluation(
    *,
    dataset_path: Path,
    mode: str,
    max_live_calls: int,
    environ: Mapping[str, str],
    runner: Runner | None = None,
) -> Path:
    """Run injected evaluation clients and persist only a redacted local report."""
    if mode not in _MODES or type(max_live_calls) is not int or not 1 <= max_live_calls <= 20:
        raise LiveEvalCommandError("invalid manual evaluation arguments", source="configuration")
    try:
        cases = load_live_eval_dataset(resolve_live_eval_dataset(dataset_path))
    except LiveEvaluationValidationError as error:
        raise LiveEvalCommandError("manual evaluation dataset is invalid", source="dataset") from error
    settings = load_manual_llm_settings(environ)
    budget = LiveCallBudget(limit=max_live_calls)
    planner = LLMQueryPlanner(settings)
    reranker = LocalLLMReranker(settings)
    outcomes = (runner or _default_runner)(
        cases, mode=mode, budget=budget, planner=planner, reranker=reranker
    )
    if len(outcomes) != len(cases) or any(result.scope_leak_count for _, result in outcomes):
        raise LiveEvalCommandError("manual evaluation rejected unsafe results", source="unsafe_scope")

    reports = [
        json.loads(
            LiveEvaluationReport(
                case_id=case.case_id,
                prompt_version=ROUTER_PROMPT_VERSION,
                mode=mode,
                accepted_tools=result.accepted_tools,
                planner_calls=result.planner_calls,
                reranker_calls=result.reranker_calls,
                scope_leak_count=result.scope_leak_count,
                passed=result.passed,
                degradation=result.degradation,
                candidates_truncated=result.candidates_truncated,
            ).to_json()
        )
        for case, result in outcomes
    ]
    output_dir = _safe_report_directory()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_version = "".join(
        character if character.isalnum() or character in {"-", "."} else "-"
        for character in ROUTER_PROMPT_VERSION
    )
    filename_stem = f"live_llm_eval_{safe_version}_{timestamp}"
    return _create_report(
        output_dir,
        filename_stem,
        json.dumps({"reports": reports}, ensure_ascii=False, indent=2),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Read process environment only for an explicitly invoked manual command."""
    try:
        args = parse_args(argv)
        report_path = run_manual_live_evaluation(
            dataset_path=Path(args.dataset),
            mode=args.mode,
            max_live_calls=args.max_live_calls,
            environ=os.environ,
        )
    except SystemExit as error:
        return 0 if error.code == 0 else 2
    except LiveEvalCommandError as error:
        code = _REDACTED_ERROR_CODES.get(error.source, "unexpected_failure")
        print(code, file=sys.stderr)
        return 2 if code != "unexpected_failure" else 1
    print(json.dumps({"report_name": report_path.name, "status": "completed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Extractor integration contract

## Shared boundary

The extractors consume `core.models.MemoryEvent` and return
`core.models.MemoryCandidate`. They do not alter `core/constants.py`,
`core/models.py`, or the Phase 0 SQLite schema.

The B-side extractors are compatible with A-side events produced by
`ingestion.collector.create_raw_event()` followed by
`ingestion.adapter.raw_event_to_memory_event()`.

Callers must provide a non-empty `user_id`, `session_id`, and `task_id` for
tenant isolation and concurrent task separation. Timestamps must be UTC or
timezone-aware values accepted by `MemoryEvent`.

## Extractor semantics

- `PreferenceExtractor` and `KnowledgeExtractor` emit user-scoped candidates.
  Template evidence is never aggregated across users.
- `WorkflowExtractor` groups by `(user_id, session_id, task_id)` and orders
  events by `(timestamp, event_id)` before deriving a workflow. A workflow is
  emitted only when its evidence-based `metadata.reproduction_rate` is at
  least `0.8`. Repeated identical workflows merge evidence into
  `metadata.occurrence_count` rather than dropping later observations.
- `ToolExtractor.calculate_tool_success_rate()` uses
  `success_count / completed_invocation_count`. Calls without terminal results
  are exposed as `unknown_count` and excluded from the denominator, preventing
  incomplete telemetry from being reported as failures.
- `EnvironmentExtractor.extract_from_tool_output()` accepts a dictionary.
  It supports common keys such as `downloads`, `documents`, `locale`,
  `installed_software`, `applications`, and `os_version`. Home-directory
  usernames in paths are normalised to `~`; credential-like keys are ignored.

`MemoryCandidate.key` and `candidate_id` are deterministic for an identical
user/type/key input. The downstream storage owner remains responsible for
upserting records by the candidate key or another approved identity rule.

## Verification

Runtime code uses only the standard library. For development tests:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -v
python -m coverage run -m pytest tests -q
python -m coverage report -m
```

Validated locally with Python 3.13.5, pytest 8.3.4, and coverage 7.14.3.

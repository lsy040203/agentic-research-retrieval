# ARR Project Guide Documentation Design

## Goal

Document the implemented ARR research-memory foundation without presenting
planned B1-E1 capabilities as completed work. The documentation must help a
reviewer understand the P0/A1/A2 boundary, data flow, and source ownership.

## Deliverables

1. Add a compact ASCII architecture overview to `DEV_SPEC.md`.
2. Add `PROJECT_GUIDE.md` at the repository root as the detailed explanation.

## Content Boundary

Completed scope is limited to P0, A1, and A2:

- P0: pytest baseline and demo JSONL fixture.
- A1: research-domain contracts and HTTP schemas.
- A2: isolated SQLite persistence, links, auditing, and scoped lifecycle
  operations.

B1-E1 remain explicitly labelled as planned work. The guide must not claim
that evidence retrieval, Agentic routing, approval services, research API, or
evaluation are implemented.

## DEV_SPEC.md Addition

Insert one ASCII diagram immediately before the architecture/file-ownership
section. It will show the completed path:

```text
ScopeKey -> ResearchMemory -> ResearchStore -> research_memory.db
                                      |-> links to legacy MemoryRecord IDs
                                      `-> immutable audit events
```

The diagram will also label B1-E1 as planned downstream stages.

## PROJECT_GUIDE.md Structure

1. Project position and implementation status.
2. ASCII architecture and P0/A1/A2 data flow.
3. Directory tree limited to the relevant implemented files.
4. Responsibilities of the domain model, API schemas, configuration, store,
   links, audit log, and tests.
5. Key guarantees: five-dimensional scope isolation, separate database,
   parameterized SQLite transactions, idempotent audit events, UTC timestamps,
   and no writes to legacy memory storage.
6. A clearly separated B1-E1 roadmap table.

## Non-Goals

- No production-code, test, dependency, or database-schema changes.
- No status changes to tasks.
- No implementation claims beyond verified P0/A1/A2 artifacts.

## Verification

- ASCII diagrams render as plain text in Markdown.
- Every described implemented file exists in the repository.
- Every planned file is labelled planned/not implemented.
- No `TODO`/`TBD` placeholders or contradictory status claims.

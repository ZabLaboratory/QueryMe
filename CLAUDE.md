# QueryMe

@../../docs/rules/git.md
@../../docs/rules/security.md
@../../docs/rules/agents.md
@../agents/_shared/architecture.md
@../agents/_shared/conventions.md
@../agents/_shared/deploy.md
@../agents/_shared/projects.md

## Description

QueryMe is the **shared query toolkit** consumed by every DB-bearing
service in the Zablab platform (ZabTruth, ZabRanking — read/write ;
ZabAuth, ZabCam, ZabCanvas, Orion, Blue — read-only). It carries :

- the **descriptor schema** that blueprints emit
  (`FROM`/`WHERE`/`JOIN`/`SELECT`/`ORDER`/`LIMIT` — no raw SQL),
- the **compiler** that turns a descriptor into a SQLAlchemy
  `Select` statement against the consuming service's metadata,
- the **validator** that whitelists tables and columns against the
  service's schema declaration before compilation,
- the **schema descriptor** types that `GET /<svc>/api/v1/_schema`
  returns (table list, column types, relations, writability).

QueryMe is a pure library — no FastAPI, no DB connection, no I/O.
It is imported by services as a git-pinned dependency.

Reference : `docs/adr/001-positionnement-et-architecture.md`.

## Stack

- **Runtime**: Python 3.11+
- **Validation**: Pydantic V2
- **Compilation target**: SQLAlchemy 2.0 (Core, sync types ; the
  consuming service runs the statement against its own async engine)
- **Package manager**: `uv`
- **Linter / formatter**: `ruff`
- **Type checker**: `mypy` (strict)

## Setup local

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest
# or: make all
```

## Folder layout

```
QueryMe/
├── .github/
│   ├── CODEOWNERS
│   └── workflows/
│       └── ci.yml
├── src/queryme/
│   ├── __init__.py
│   ├── descriptor.py      # QueryDescriptor + WhereClause + JoinClause + OrderClause
│   ├── schema.py          # SchemaDescriptor + TableDef + ColumnDef + RelationDef
│   ├── validator.py       # validate_against_schema()
│   └── compiler.py        # compile_query()  (stub in v0.1.0)
├── tests/
│   ├── __init__.py
│   ├── test_descriptor.py
│   ├── test_schema.py
│   └── test_validator.py
├── pyproject.toml
├── Makefile
└── CLAUDE.md
```

## Versioning

Semver. Each consumer service pins a specific tag in its
`pyproject.toml` :

```toml
[project]
dependencies = [
  "queryme @ git+https://github.com/ZabLaboratory/QueryMe.git@v0.1.0",
]
```

For local dev, services can override via `[tool.uv.sources]` to a
path dep — never commit that override.

## Scope of v0.1.0 (this scaffold)

- `QueryDescriptor` Pydantic model implementing the canonical
  modal-compatible form locked by ADR 001 §7 :
  `from → (where|join)* → select → (order|limit)?`.
- `SchemaDescriptor` Pydantic model — what every service's
  `_schema` endpoint returns.
- `validate_against_schema()` — checks tables / columns / operators
  against the schema whitelist, returns a list of structured issues.
- `compile_query()` — **stub** raising `NotImplementedError`.
  Real compilation is the next milestone (v0.2.0).
- No `_mutate` types yet — read-only descriptor only.

## Out of scope

- Mutation descriptors (`InsertDescriptor`, `UpdateDescriptor`,
  `DeleteDescriptor`) — Phase 2.x.
- UNION / multi-FROM — graph-mode-only territory per ADR 001 §7.
- Type-coercion rules between descriptor values and column types —
  delegated to SQLAlchemy at compile time.

## Resolution criteria

Conformity with `docs/rules/git.md`. A branch is **resolved after merge**
when :

1. **Squash merge** done on `main` by the maintainer.
2. **All CI jobs green** on the merge commit (lint, typecheck, test,
   deps-audit, secret-scan, lockfile-check, codeowners-check).
3. **No regression** on `.health.json` (no `critical` / `high`).
4. **Branch deleted** on the remote after the squash.

## Decisions

- 2026-04-27 — Initial scaffold. Pydantic models for descriptor +
  schema, real validator, stub compiler. Library-only — no I/O. Follows
  ADR 001.

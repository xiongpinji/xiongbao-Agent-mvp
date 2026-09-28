# Objective
Implement paired034 safe calendar/catalog/tag schema upgrade, without losing existing todo/comments/images.

# Why
Approved C1 requires composite project-owned references. Old SQLite parent table must rebuild without renaming the referenced old parent or causing CASCADE loss.

# Scope
Ownership: src/octop/infra/db/migrations/034_project_todo_fields.sql and .pg.sql; src/octop/infra/db/project_plan_seed.py; only034 wiring in src/octop/infra/db/migrate.py; tests/unit/db/test_project_todo_fields_migration.py; only old watermark assertion in tests/unit/db/test_project_todo_comments.py.
Do not write project_todos/projects/services/app/api/dashboard or old migrations. Other workers own them. You are not alone; preserve their changes.

# Files to inspect
AGENTS.md; C1 contract; migrations020/031/032/033; pool.py SqlitePool.connect(transaction=True); migrate.py _apply_sqlite_migration/run_migrations/_max_discovered_version; test_project_todo_comments.py migration033 rollback fixture.

# Implementation guidance
TDD upgrade from033 and fresh schema. All tables and limits/date fields/composite keys per approved C1. Expose seed_todo_catalog(conn,project_id,ts), idempotent same-connection old/new project seed revision1 + four options.
SQLite: outer same connection lock, PRAGMA foreign_keys OFF before BEGIN IMMEDIATE, create replacement parent/complete copy with internal id and all old columns, drop old formal parent then rename replacement, recreate old/new indices. ChildFKs stay formal project_todos. Check original row/child counts, FK target, foreign_key_check and schema watermark before commit, rollback any exception, finally restore+verifyFKON. Avoid executescript implicit commits.
PG: paired SQL constraints, backfill default rows via supported SQL/helper in migration transaction. No changing old033. All nullable field and actual calendar checks; SQL enforcement shape/range/start<=due.
Inject failure after copy, after swap, before watermark and replay; preserve todo/version/descriptionFormat/comment/image/usage, filebytes external unchanged. Guard genuinely existing complete migration before replay, not waterline only.

# Constraints
No commits, pushes, releases, deployments, credentials, dependency changes, reset/clean/deletion of others, broad format or nested subagents. New checkout only. Worker may run local SQLite tests, no paid providers or real customer DB.

# Acceptance criteria
Fresh/upgrade/replay/partial-fault fully tested; active name partial uniqueness, total/FKs per contract; all original child public IDs and rawmetadata survive; FK state always restored. Return precise helper signatures and any limitations. Do not claim PG concurrency or backup acceptance from mocks.

# Validation
PowerShell cwd checkout, set UV_PROJECT_ENVIRONMENT to ../030-windows-path-qa/.venv absolute; PYTHONPATH newcheckout/src.
uv run --no-sync pytest tests/unit/db/test_project_todo_fields_migration.py tests/unit/db/test_project_todo_comments.py -q
uv run --no-sync ruff check src/octop/infra/db/project_plan_seed.py src/octop/infra/db/migrate.py tests/unit/db/test_project_todo_fields_migration.py
uv run --no-sync ruff format --check src/octop/infra/db/project_plan_seed.py src/octop/infra/db/migrate.py tests/unit/db/test_project_todo_fields_migration.py tests/unit/db/test_project_todo_comments.py
Save RED/GREEN output outsidetracked repo or named local QA dir; no bulk git operations.

# Final report
DONE/NEEDS_CONTEXT; exactchangedfiles, RED/GREEN commands+exit+counts, preserved IDs/FK/rollback evidence, assumptions/deviations and PG/backup checks not run. No commit.

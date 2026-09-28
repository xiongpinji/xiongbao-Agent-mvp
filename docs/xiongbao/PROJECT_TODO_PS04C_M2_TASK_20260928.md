# Objective
Implement approved C1 directory CRUD and all todo date/priority/tag DTO writes atomically with existing ACL/version semantics.

# Why
Today todo create/update reread after commit and only status/assignee exist. Shared real fields must support table/board/detail and future query without mixed version/tag snapshots.

# Scope
Own backend repos/project_todos.py, projects.py, project_activity.py; new repos/project_todo_catalog.py and project_plan_locks.py; infra/projects/todos.py/activity.py/new todo_catalog.py; api/routers/project_todos.py/project_activity.py/new project_todo_catalog.py; db/services.py/api/app.py; backend i18n zh/en safe event text; new C1 db/API tests, existingstrictDTO-key and minimal servicefixture adaptations.
Do not edit migrate.py/034/project_plan_seed.py (M1 owner), dashboard (M3), plan_definition.py (Codex/Q1). You are not alone; don't revert others; no new independent schema numbers.

# Files to inspect
AGENTS.md; complete C1 contract; ProjectTodoRepo _locked_member_roles/create/update/bulk/get/list; ProjectRepo create_with_owner/remove_member/set_member_role/update_project/expert_replace; TodoView/_view/ProjectTodoService; project_todos API body model_fields_set/payload; services.py container; app.py registration; project_activity repo/service/router safe projections; integration todos/comments helpers.

# Implementation guidance
TDD new route404 and fieldstructure RED first. M1 provides seed_todo_catalog(conn,project_id,ts) and paired034, do not write its files; coordinate schema availability for GREEN.
All C1 endpoints, strict extra keys/int-no-bool, colors/names/limits/softarchive/order/revision. Seed in future create transaction only. Catalog reads boundedall/options and server_today/server_timezone config defaulttimezone.
Members sorted→project→catalog/items→todoIDs sorted; configwrites reject archived409 but existingtodo/comment policies not uniformly changed. Same locked transaction checks dates after waiting (clock defaulttimezone), mergedstart/due, oldoverdueunchanged permitted.
Todo Row/View/DTO add start_date/due_date/priority_id/tag_ids/catalog_revision required; create optionalstatus. Exact omit/null semantics/catalog revision pairing, no_change legacy400 and bulkrepeat increments preserved. Tags/ref linkage+events+rowDTO assembled before commit, no postcommitself.get.
remove_member current UPDATEmany unsorted: ordered lockrows then clearassignee/version/event withinmembership lock order; preserve private task cleanup and dates/catalog references.
New catalog_event and new todo fieldnames safe projection across repo/domain/router/backend translations, no payloadJSON/name/title/body.
Reads consistency with catalogrevision/taglinks and currentmembership must be proven in transaction, not precheck + otherconnection.

# Constraints
User authorized Codex/native this slice only. No commits/push/deploy/provider calls/credentials/dependency changes/nested agents/reset/clean/unrelated refactors. Other workers changing disjointpaths. No enabling030filemode or touching045frontend.

# Acceptance criteria
Full C1 contract ACL/errors/quotas/lifecycle/dates/compatibility/event/snapshot. LocalSQLite focused tests pass, exactoldtests retained. PG two-pool and browser performed independently later, explicitly not claimed. No fake default catalog fallback.

# Validation
UV_PROJECT_ENVIRONMENT absolute ../030-windows-path-qa/.venv and PYTHONPATH checkout/src, uv run --no-sync pytest.
uv run --no-sync pytest tests/integration/test_project_todo_catalog_api.py tests/integration/test_project_todo_fields_api.py tests/unit/db/test_project_todo_catalog.py tests/integration/test_project_todos_api.py tests/integration/test_project_todo_comments_api.py tests/integration/test_projects_api.py tests/integration/test_project_activity_api.py tests/unit/db/test_project_todos.py tests/unit/db/test_project_repo.py tests/unit/db/test_project_activity.py -q
uv run --no-sync ruff check src/octop/infra/db/repos/project_todos.py src/octop/infra/db/repos/projects.py src/octop/infra/db/repos/project_activity.py src/octop/infra/db/repos/project_todo_catalog.py src/octop/infra/db/repos/project_plan_locks.py src/octop/infra/projects/todos.py src/octop/infra/projects/activity.py src/octop/infra/projects/todo_catalog.py src/octop/api/routers/project_todos.py src/octop/api/routers/project_activity.py src/octop/api/routers/project_todo_catalog.py src/octop/infra/db/services.py src/octop/api/app.py tests/integration/test_project_todo_catalog_api.py tests/integration/test_project_todo_fields_api.py tests/unit/db/test_project_todo_catalog.py
uv run --no-sync ruff format --check src/octop/infra/db/repos/project_todos.py src/octop/infra/db/repos/projects.py src/octop/infra/db/repos/project_activity.py src/octop/infra/db/repos/project_todo_catalog.py src/octop/infra/db/repos/project_plan_locks.py src/octop/infra/projects/todos.py src/octop/infra/projects/activity.py src/octop/infra/projects/todo_catalog.py src/octop/api/routers/project_todos.py src/octop/api/routers/project_activity.py src/octop/api/routers/project_todo_catalog.py src/octop/infra/db/services.py src/octop/api/app.py tests/integration/test_project_todo_catalog_api.py tests/integration/test_project_todo_fields_api.py tests/unit/db/test_project_todo_catalog.py
No globalautofix.
Update _TODO_KEYS rather than remove exactasserts. Use actualenv/_base from test_project_todos_api.

# Final report
Exactfiles/interfaces; valid RED and GREEN outputs, tests counts/exits, any concurrent changes accommodated, exceptions/remainingtests. Return without commits; fixedbyte independent spec and quality reviews follow.

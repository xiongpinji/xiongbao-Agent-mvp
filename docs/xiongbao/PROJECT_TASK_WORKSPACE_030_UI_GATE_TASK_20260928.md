# Objective

In the existing 030A controlled-file task, make the embedded WorkspaceDrawer load the current owner's internal runtime tree after the chat route has verified the thread. Keep ordinary Agent pickers and ordinary workspace drawers unable to resolve internal runtimes.

# Why

At base `849bb756`, `AgentContext.tsx` correctly filters `internal=true` out of `useAgent().agents`. `WorkspaceDrawer.tsx:268-270` only searches that filtered list, so `workspaceReady` is false and its root request at line ~353 never runs. A Vitest behavior repro with `agents=[]` and a running internal card through `getChatAgentById` fails because the request spy has zero calls. The approved 030A contract allows raw resolution only for a verified existing thread, not globally.

# Scope

Allowed changed files only:

- `dashboard/src/pages/Agent/Workspace/components/WorkspaceDrawer.tsx`
- `dashboard/src/pages/Agent/Workspace/components/WorkspaceDrawer.privateTask.test.tsx` (new)
- `dashboard/src/pages/Chat/components/ChatDockPanel.tsx`
- `dashboard/src/pages/Chat/components/ChatDockPanel.workspaceFiles.test.tsx`

No Python, schema, routes, `AgentContext.tsx`, packages, locales, or other UI files. Do not enable `PROJECT_TASK_FILES_MODE_ENABLED`, alter server ACLs, expose internal cards to ordinary selectors, or expand the six allowed model tools. This task addresses directory readiness only; unsupported create-folder/move/delete/archive controls remain a separately recorded UI gap.

# Files to inspect

- `AGENTS.md` sections 1, 3, 5, 6, 10: repository checks and narrow-edit rules.
- `docs/xiongbao/PROJECT_TASK_WORKSPACE_030_CONTRACT.md` lines 7-14 and 39-40: approved internal-card privacy and frontend owner-thread rule.
- `dashboard/src/context/AgentContext.tsx` around `agents` filter and `getChatAgentById`: raw owner card versus ordinary projection; do not edit it.
- `dashboard/src/pages/Chat/index.tsx` around `useInternalTaskRoute`, `chatAgentId`, and `<ChatDockPanels>`: history verifies thread ownership before an internal id becomes available; do not edit it.
- `dashboard/src/pages/Chat/components/ChatDockPanels.tsx`: existing `privateTask` relay; do not edit it.
- `dashboard/src/pages/Chat/components/ChatDockPanel.tsx` around `<WorkspaceDrawer>`: add the narrow explicit private-task marker to this caller.
- `dashboard/src/pages/Agent/Workspace/components/WorkspaceDrawer.tsx` around props, `activeAgent`, `workspaceReady`, and `refreshRoot`: fix only resolution for the marked private task.
- `dashboard/src/pages/Chat/components/ChatDockPanel.workspaceFiles.test.tsx`: existing dock mocks and assertions; extend only if needed to prove marker relay.
- Read-only RED specimen in Windows main checkout: `/mnt/c/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/030-windows-path-qa/dashboard/src/pages/Agent/Workspace/components/WorkspaceDrawer.privateTask.test.tsx`. Copy or write equivalent tests inside this worker checkout; the source test is uncommitted and must not be edited in place.

# Implementation guidance

1. Add a Vitest test in the allowed new test file that renders the real embedded WorkspaceDrawer under `MemoryRouter` and Ant Design `App`; mock `FileViewer` to avoid unrelated pdfjs DOMMatrix loading. Mock `useAgent` with ordinary `agents=[]` and `getChatAgentById(id)` returning `{agent_id:id,internal:true,state:"running"}` only for the explicit internal id. With `privateTask` marked, assert the root request exactly targets `/agents/private-runtime/workspace/tree?path=/&from_workspace=true`. Capture a true behavioral RED with zero requests before changing product code.
2. Add a negative test: the same internal id through ordinary WorkspaceDrawer without the marker must neither resolve the raw card nor request the tree. Cover an ordinary running Agent if the existing tests do not already do so.
3. Introduce a default-false `privateTask` prop on WorkspaceDrawer and pass the existing ChatDockPanel `privateTask` value only from the chat dock caller. In the drawer, use `getChatAgentById` only when the marker is true and the returned card is the matching internal runtime; otherwise keep using the filtered `agents` list. If the internal card is missing or not ready, preserve the existing no-request state. The marker is a frontend presentation signal from the already verified chat route, not a new auth claim. All HTTP authorization stays on the server.
4. Re-run focused tests to GREEN, then adjacent dock/private-task tests and TypeScript/build. Keep the diff surgical. If the route's verification semantics conflict with this approach, stop and ask in the task question channel; do not silently widen `getChatAgentById` use.

# Constraints

You own only the four allowed frontend files. You are not alone in the codebase: preserve other agents' edits and do not revert or reformat unrelated work. Do not spawn native subagents; Agent Orchestrator owns routing, retries, and review. Do not commit, push, release, deploy, change credentials, install packages, remove files, or run destructive cleanup. Tracked files begin clean at the fixed base. The only pre-existing untracked path is a coordinator-owned `dashboard/node_modules` symlink to another checkout with the same `package-lock.json` hash; the launcher records it with `--allow-dirty`. Do not modify that symlink or dependency tree or attribute it to your work. Report any unavailable command instead of claiming it passed.

# Acceptance criteria

- Verified private-task dock passes the marker and requests its own root tree once an owner-scoped running internal card exists.
- Without the marker, the same internal id remains unavailable and issues no workspace tree request.
- `useAgent().agents` still excludes internal cards, and ordinary Agent drawers continue using that projection.
- Missing, mismatched, or not-ready internal cards produce no private tree request.
- No backend/ACL/mode changes or unrelated frontend code changes.

# Validation

From `dashboard/`, run `npx vitest run src/pages/Agent/Workspace/components/WorkspaceDrawer.privateTask.test.tsx src/pages/Chat/components/ChatDockPanel.workspaceFiles.test.tsx src/pages/Chat/components/ChatDockPanels.privateTask.test.tsx`, then `npx tsc -b`, `npx eslint src/pages/Agent/Workspace/components/WorkspaceDrawer.tsx src/pages/Agent/Workspace/components/WorkspaceDrawer.privateTask.test.tsx src/pages/Chat/components/ChatDockPanel.tsx src/pages/Chat/components/ChatDockPanel.workspaceFiles.test.tsx`, `npx prettier --check` on the same four files, and `npm run build`. Report exact exit codes. Do not run real provider or browser tests from this job; Codex owns those acceptance layers.

# Final report

List changed files and precise behavior, show RED and GREEN test results plus each validation exit code, identify any skipped or unavailable checks, note assumptions/deviations/remaining risks, and identify any instruction you could not follow. Do not describe your own diff as accepted or merged; Codex will review it independently.

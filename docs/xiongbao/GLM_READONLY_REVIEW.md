# GLM-5.3 只读审查原文

Agent Orchestrator 任务：`qwen-code-review-20260923-041251-ce1f37`。审查基于首批集成快照；其后 Codex 修复了报告指出的可点击品牌入口、旧品牌登录/移动端标识与 PWA 清单。以下为审查原文，其行号以该快照为准。

All evidence is gathered. Composing the final acceptance review report.

---

# Xiongbao-Agent Phase 1 — Independent Read-Only Acceptance Review (GLM lane, item-003)

## 0. Review method and tooling constraints (must read first)

- **No shell tool was available in this reviewer session** (the tool registry exposes only read-only file tools). Therefore `git diff`, `git status --porcelain`, `git diff --check` and the `channel.py` phase-update script could **not be executed**. The change-set inventory below is taken from the frozen git snapshot captured at conversation start (HEAD = baseline `c7f82867`, all changes unstaged), cross-validated by glob mtime ordering and direct source reads. That snapshot lists exactly 21 modified + 6 untracked paths, all under `dashboard/` and `docs/xiongbao/`.
- **Zero writes were performed**: every tool call was `read_file` / `glob` / `grep_search` / `tool_search` / `list_agents`. The worktree is byte-identical to the state at review start (I cannot *demonstrate* this with `git status --porcelain` — disclosed as a limitation, not claimed as executed).
- **No tests, tsc, lint or build were run** (per task constraints; those may write caches). All test-quality statements below are from reading test source; other agents' claimed results are treated as unverified.
- Nothing was committed, staged, pushed, or delegated to subagents.

## 1. Verdict recommendation

**Conditional accept.** Both lane contracts are implemented within scope; thread/agent data isolation, authorization semantics, and zh/en key parity all hold under source inspection; the new tests are genuinely behavioral. One **P1 contract deviation** (the sidebar brand avatar was required to be clickable and is not) and several P2 cleanups should be fixed or explicitly waived by Codex before final acceptance. Final acceptance additionally requires the executed test/typecheck/build run and a browser smoke check that this lane could not perform.

## 2. Change-set inventory and scope verification

Classified against the two lane whitelists in `docs/xiongbao/PHASE1_PLAN.md`:

| Class | Files |
|---|---|
| Lane 1 — brand/theme/welcome | `dashboard/index.html`, `src/layouts/Sidebar.tsx`, `Sidebar.module.less`, `src/pages/Chat/components/WelcomeScreen.tsx`, `src/pages/Chat/chatWelcome.partial.less`, `src/styles/appearanceStorage.ts` + `.test.ts`, `src/styles/bootTheme.test.ts`, `src/locales/{zh,en}.json`, `dashboard/public/xiongbao-logo.png` (asset), `WelcomeScreen.scenarios.test.tsx` (new) |
| Lane 2 — right overview/artifacts | `src/pages/Chat/hooks/useChatDockPanel.ts` + `.test.ts`, `components/ChatDockPanels.tsx` + `.keepAlive.test.tsx`, `components/ChatDockPanel.tsx` + `.addTab.test.tsx`, `components/ChatDockOverview.tsx/.test.tsx` (new), `components/ChatArtifactList.tsx/.test.tsx` (new), `pages/Chat/index.tsx`, `pages/Chat/index.module.less` |
| Integration/lint cleanup (out of lane whitelists, see P2-7) | `hooks/chatStore.ts`, `src/pages/Agent/Channels/components/constants.test.ts` |
| Docs | `docs/xiongbao/{PHASE1_PLAN,PHASE1_DESIGN,WORKBUDDY_PARITY}.md` |

**Scope result:** no `src/octop/**` (backend or built dashboard artifact), no `LICENSE`, no config/secret/credential changes appear in the change set. The two lanes do not overlap files; the only shared seams are `index.tsx` (renders both `WelcomeScreen` and `ChatDockPanels`) and the locale files (lane-1 keys plus Codex-integrated lane-2 keys) — both verified coherent. Note the built SPA under `src/octop/dashboard/` is intentionally untouched, so it is stale until integration runs `make build-frontend`.

## 3. Findings

### P0 — none found
No cross-thread/cross-agent data leak, no auth bypass, no i18n key break, no out-of-scope backend write was found (evidence in §4).

### P1-1 — Sidebar brand avatar is not clickable (contract deviation)
- **Contract:** `docs/xiongbao/PHASE1_PLAN.md` 任务1 step 3: “给 Sidebar 添加**可点击**的熊宝头像与双语产品名，保留现有导航处理器和权限判断”.
- **Actual:** `dashboard/src/layouts/Sidebar.tsx:451-477` (`brandInner`: `<img>` + name span, no button, no handler); desktop brand container `Sidebar.tsx:725-745` carries only `DESKTOP_DRAG_REGION_CLASS` and has no `onClick`; mobile drawer header (`Sidebar.tsx:610-640`) likewise. Per `src/styles/layout.css:31-44`, `.octop-desktop-drag` only arms window-dragging inside the Wails shell; in a browser the class is inert.
- **Repro:** fresh desktop or mobile drawer → click the panda avatar or “熊宝-Agent” name (expanded or collapsed rail) → nothing happens (browser) / window-drag gesture (Wails shell). Expected per contract and WorkBuddy parity: brand click navigates to task home (e.g. `/chat`) or toggles the rail.
- **Also missing:** no automated test for the Sidebar brand at all (the plan allowed "属于上述组件的新定向测试”).
- **Minimal repair:** wrap `brandInner` in a `<button type="button">` with `onClick={() => navigate("/chat")}` (reuse the existing `handleNavigate` semantics), add `octop-desktop-no-drag` so `layout.css:37-44` keeps it clickable inside the drag region, and add one Sidebar test.

### P2 findings (ordered)
1. **Dead CSS-module reference** — `Sidebar.tsx:731` applies `styles.sidebarBrand`, but no `.sidebarBrand` rule exists in `Sidebar.module.less` (grep: single occurrence repo-wide). At runtime the className renders as `"undefined octop-desktop-drag"`. Harmless visually, but a wart introduced by this change and it removes any cursor/affordance styling for the brand.
2. **PWA install surface still "Octop"** — `dashboard/public/manifest.json` keeps `name/short_name: "Octop"`, old `pwa-192.png`/`pwa-512.png`/`apple-touch-icon.png` icons, `background_color #0f1117`, while `index.html` now brands favicon/apple-touch-icon/title as 熊宝-Agent. An installed PWA will still identify as Octop. `manifest.json` is outside both lane whitelists — flag for an explicit integration decision (phase-2 candidate).
3. **Orphaned CSS** — `.welcomeLogoIcon` (`chatWelcome.partial.less:53`) has no remaining TSX reference after the mascot was replaced by the static brand logo.
4. **Logo binary unverifiable in this lane** — `dashboard/public/xiongbao-logo.png` exists (newest file in `public/`), but image decoding is unsupported here. Only `ChatDockOverview.tsx:52-56` has an `onError` fallback (`/pwa-192.png`); the splash (`index.html`), `Sidebar.tsx:455-467` and `WelcomeScreen.tsx:201-207` have none — a corrupt asset would show broken images on 3 of 4 surfaces. Needs one browser check in Codex's lane.
5. **Expert quick cards hidden on non-default scenario** — `WelcomeScreen.tsx:264-273` renders `WelcomeQuickCards` only when `isDefaultScenario` (office). Baseline always rendered them; the plan only said “保留现有 quickCards 流程”. Plausibly intentional decluttering — confirm intent or restore.
6. **Quick cards without expert name still auto-send** — preserved baseline behavior (`WelcomeQuickCards.tsx:46-51`, unchanged file) sends plain quick-card prompts immediately, while `PHASE1_DESIGN.md` acceptance #3 says welcome cards “仅预填，不越过用户发送动作”. The *scenario chips* comply strictly; clarify which card class that sentence governs.
7. **Out-of-whitelist edits, benign but unenumerated** — `chatStore.ts` and `Agent/Channels/components/constants.test.ts` are outside both lane file lists. Current contents are consistent with the two pre-existing lint errors documented in `WORKBUDDY_PARITY.md` (redundant `Boolean` at `chatStore.ts:~1140`, unused import at `constants.test.ts:5` — all imports now used), but without git this lane cannot prove the diffs contain *only* those fixes.
8. **Minor:** boot splash ring is hard-coded gold regardless of stored palette (brand choice, cosmetic); static `<title>熊宝-Agent</title>` is corrected pre-paint by the language-aware inline script (`index.html` body script).

## 4. Inspected behaviors (source-traced, not executed)

- **Fresh desktop / mobile:** desktop starts with `dockOpen=true`, single `overview` tab (`useChatDockPanel.ts:118-125`); mobile starts collapsed with no tabs — both asserted in `useChatDockPanel.test.ts`.
- **First paint & saved light theme:** `index.html` boot script (key `"theme"`, same JSON shape as `appearanceStorage.ts`/`THEME_STORAGE_KEY`) defaults to **dark** when unset, honors stored `light`/`dark`/`system` (legacy plain string and JSON), and sets `data-theme` before the splash CSS applies. `ThemeContext.tsx:74-92` re-applies the identical resolution and keeps `theme-color` metas in sync. `appearanceStorage.test.ts` covers: dark+amber default, stored light wins, legacy migration, invalid JSON fallback.
- **Suggestion tab click prefill:** scenario chips call `onPromptClick(t(prompt), { prefill: true })` (`WelcomeScreen.tsx:248-251`); handler `index.tsx:821-831` sets the composer text and returns **without sending**. Tab list has full ARIA/roving-tabindex keyboard support; `WelcomeScreen.scenarios.test.tsx` asserts switch + prefill-only behavior.
- **Expert quick cards:** still fed by `useExpertChatWelcome(activeAgent)` and rendered (office tab only — see P2-5); layout probe preserved.
- **Overview close/reopen:** every tab has a close button (`ChatDockPanel.tsx` tabBar); closing the last tab closes the dock; `handleClose` keeps non-toolUi tabs for keep-alive; a desktop float button (`index.tsx:1373-1388`) and the "+" add-tab menu (`ChatDockPanel.tsx` `DockAddTabButton`) both reopen the overview. Covered by hook + addTab tests.
- **Artifacts from current thread only:** `index.tsx:498-519` derives `threadArtifacts` from `composerSession` (`sessions.find(id === activeThreadId)`), refreshed on `streamEnd`/file-tool `toolDone` via `fetchAndSyncSessionArtifacts(agentId, threadId)` → thread history API (`useSessions.ts:199-225`). The artifacts tab receives **only** this list — asserted by `ChatDockPanel.addTab.test.tsx` ("lists only thread artifacts … not opened files"). `ChatArtifactList.test.tsx` covers empty state, dedupe, click-through, thread switch.
- **Path dedupe:** `openFileAt` dedupes by `dockFileTabId` = `file:<canonicalizeDockFilePath(path, agentId)>`; agent-home absolute, truncated `/.octop/agents/…` and relative forms collapse to one key (`dockFilePath.ts`, asserted in hook + artifact tests).
- **Thread/Agent switch isolation:** agent A→B resets tabs to the fresh overview state (desktop) / clears and stays collapsed (mobile), ignoring null↔id first-paint races (`useChatDockPanel.ts:120-135`, both tested). Thread switch swaps artifact content via the thread-scoped session record. `sharedExpertViewer` additionally gets `artifacts=[]`, no artifacts/workspace/files openers (`index.tsx:457-471, 1655-1657`) — a permission hardening, not a regression.
- **Authenticated preview:** artifact/file clicks go through `openFileAt` → `FilePanelContent.tsx` → the shared authenticated `request`/`requestBlob` client on `/agents/{agentId}/workspace/file?path=…` (existing ownership-checked API; no new endpoints, no client-side local file reads). `ChatDockFileList` downloads use the same `requestBlob` path.
- **Existing file/browser/terminal tabs:** unchanged openers/toggles, keep-alive on dock close and mode switch (`ChatDockPanels.tsx:46-48, 85-92`, tested in `keepAlive.test.tsx`).
- **i18n:** all keys referenced by changed components exist in **both** locales — `chat.dockOverview*/dockArtifacts*/dockAddTab` (zh/en 1503-1515), `chatWelcome.scenario*` set (zh 3454-3481 / en 3457-3484, equal block sizes), `app.brandName/app.pageTitle` (zh/en lines 3-4). `App.tsx:63` re-applies the localized page title. The Chinese `t(key, "中文")` fallbacks in lane-2 components are backed by real locale entries, so English users get English strings.
- **Brand/name display:** avatar + `t("app.brandName")` in expanded and collapsed rails and the mobile drawer; page title/favicon/splash rebranded; dock overview logo has an onError fallback.

## 5. Unverified risks and acceptance evidence gaps

1. No executed test/typecheck/lint/build evidence in this lane (by design — Codex must supply it). The new tests read as meaningful behavior tests, but red-first history is unverifiable without logs.
2. Actual browser first-paint (no flash), logo rendering, visual WorkBuddy parity (charcoal/warm-gold fidelity, spacing, focus states) — source tokens are consistent with intent (amber dark `colorLink #FBBF24` etc.), but not observed.
3. Full zh↔en locale key-tree parity beyond the keys referenced by changed components (could not be exhaustively diffed without execution).
4. `xiongbao-logo.png` binary validity (P2-4).
5. The `chatStore.ts` / `constants.test.ts` diffs are consistent-with-lint-fix but not byte-proven (P2-7).

## 6. Validation commands and exit statuses

- Executed: read-only `read_file` (30+ files incl. all changed sources/tests/locales/docs), `glob` (Chat tree, styles, public, docs, `src/octop/**` mtime sanity), `grep_search` (locale keys, CSS classes, handlers, drag-region, event emitters), `tool_search`, `list_agents`. All succeeded; **none writes**.
- Not executed (unavailable/forbidden): `git diff --check`, `git diff --stat`, `git status --porcelain`, `channel.py event/ask`, tests/build. Git status is unchanged **by construction** (zero mutating tool calls); proof-by-command unavailable in this lane.
- Phase updates to the Codex channel could not be published (no shell); this report is delivered in-band as the item-003 deliverable.

## 7. Recommendation to Codex

Fix or explicitly waive P1-1 (clickable brand, with a test) and the quick P2s (dead `sidebarBrand` class, orphaned `.welcomeLogoIcon`, decide manifest.json branding), then run the integration bar (`npm test -- --run` targeted files, `npx tsc -b`, `npm run lint`, `npm run build`, then `make all` + `make build-frontend`) and one desktop browser smoke pass covering the traced interactions in §4 before marking item-004 complete. The lane isolation, data-scoping, authorization reuse, and bilingual copy requirements of both lane contracts are otherwise satisfied on the evidence of source.

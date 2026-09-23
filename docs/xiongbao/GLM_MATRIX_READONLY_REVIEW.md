# GLM-5.3 对标矩阵只读核验原文

Agent Orchestrator 任务：`qwen-code-review-20260923-045813-425140`。本报告基于任务启动时的源码快照；后续修复另以提交和测试为准。

# Xiongbao-Agent WorkBuddy parity matrix + phase-2 plan — independent read-only review

## Verdict

**Accept with two P1 documentation corrections.** The matrix and task packets are substantively truthful and appropriately conservative. Every P0 row I could verify against source carries a status no stronger than the evidence supports — "代码及定向测试通过" is used only where code and named targeted tests actually exist, "部分实现·待桌面验收" rows match partial source reality, and the workspace/task-history/four-result-area/diff/preview gaps are all captured as 差距/待验收. Nothing in the packet implies full WorkBuddy 1:1 parity or a validated post-login desktop journey; both are explicitly listed as pending (login captcha not attempted, real desktop journey unverified, full-suite frontend failures disclosed as a gap). The two P1 defects below are undisclosed residuals that would mislead a reader on specific claims, not systemic overclaiming.

## Verified truthful (spot-checked against final source)

- **Brand row (`WORKBUDDY_PARITY.md:17`)**: Login (`pages/Login/index.tsx:252,263` + `Login.forgotPassword.test.tsx:30-38`), Setup (`pages/Setup/index.tsx:258,264`), mobile header (`layouts/Header.tsx:78,96` + test), sidebar, welcome page, `index.html` favicon/apple-touch/title/splash, `zh/en` `app.brandName`/`app.pageTitle`, and PWA `manifest.json` (name 熊宝-Agent, short_name 熊宝) are all rebranded as claimed.
- **P1 repair claim (`WORKBUDDY_PARITY.md:45`)**: clickable brand is real — `SidebarBrand.tsx` is a `<button>` with `DESKTOP_NO_DRAG_CLASS` → `navigate("/chat")`, tested in `SidebarBrand.test.tsx`; the dead `.sidebarBrand` class and orphaned `.welcomeLogoIcon` CSS from the earlier GLM review were also cleaned up.
- **Dock row**: `useChatDockPanel.ts` `initialDockTabs` (desktop `[overview]`, mobile `[]`), agent-switch reset, close/reopen float button and add-tab menu with overview/artifacts entries (`ChatDockPanel.tsx:116-129,500-506`), all tests present.
- **Artifacts row**: thread-scoped `threadArtifacts` (`index.tsx:498-510`), empty state/dedupe/click-preview (`ChatArtifactList.tsx` + `dedupeDockFilePaths`), shared-expert hardening (`index.tsx:455-470`), zh/en locale keys present at matching lines.
- **Scenario row (`:20`)**: three-scenario ARIA tablist with roving keyboard and prefill-only chips, asserted in `WelcomeScreen.scenarios.test.tsx`.
- **PWA caveat (`:66`)**: manifest indeed uses a single `xiongbao-logo.png` with `sizes:"any"`, no maskable/sized icons — accurately flagged as pending.
- **Phase-2 governance**: GLM stays strictly read-only with Codex owning integration, tests, commits and pushes (`PHASE2_TASKS.md` 审查和整合 section). ✓

## Findings (prioritized)

**P1-1 — Quick-card auto-send limitation is not called out anywhere.** `PHASE1_DESIGN.md:19` ("点击卡片只会预填任务，不自动发送") and `:31` ("欢迎卡片仅预填，不越过用户发送动作") read as governing all welcome cards, but expert quick cards without an expert name still send immediately in the shipped code: `WelcomeQuickCards.tsx:49-55` passes `prefill: Boolean(card.expertName)`, `expertName` is optional (`useExpertQuickCards.ts:27`), and `handlePromptClick` falls through to `wrappedHandleSend` when `prefill` is false and the text doesn't match the incomplete-prompt heuristics (`pages/Chat/index.tsx:820-831`, `utils/quickInputPrefill.ts:4-20`). The scenario tests render `quickCards={[]}`, so "不自动发送的定向测试通过" (`WORKBUDDY_PARITY.md:20`) is true only for scenario chips; this ambiguity was flagged by the prior GLM review (P2-6) and never resolved in the docs. **Correction:** add to the `:20` row or the `:66` boundary section: "场景建议卡片仅预填；无专家名的快捷卡片点击后仍立即发送（`handlePromptClick` 直发路径），待统一或明确豁免", and qualify `PHASE1_DESIGN.md:31` to state which card class it governs.

**P1-2 — The "安装" surface is listed as branded, but the install prompt UI still says "Octop".** `WORKBUDDY_PARITY.md:17` enumerates "登录/安装/设置/侧栏/欢迎页品牌" as covered, but `components/PwaInstallPrompt/index.tsx:55,119-120` hard-codes "将 Octop 安装为 App" / "安装 Octop / Install Octop" — and this prompt renders in the chat float actions (`pages/Chat/index.tsx:1369`) and the header (`Header.tsx:120`), i.e., first-screen surfaces, not "深层页面". Only the install *manifest* was fixed (the closing note `:66` is accurate on that). **Correction:** either rebrand the prompt copy (and move it to i18n — it's hard-coded inline, off-convention) or amend `:17` to "安装清单已换品牌，安装引导弹窗文案仍为 Octop，列入品牌扫描清单".

**P2-1 — Broken matrix link.** `PHASE2_TASKS.md:25` links to `熊宝-Agent-WorkBuddy-差距与路线图.md`, which does not exist in `docs/xiongbao/` (only 5 files); the acceptance ledger is `WORKBUDDY_PARITY.md`. Fix the href.

**P2-2 — Phase-2 A/B file ownership is deferred, and the known shared seams are not named.** Unlike `PHASE1_PLAN.md` (explicit per-lane whitelists), `PHASE2_TASKS.md` defers file lists ("具体文件在进入任务前以当前源码重新核对") and assigns only "共享的路由/locale" to Codex. But both lines plausibly need `pages/Chat/index.tsx` (1698 lines; the phase-1 shared seam) and `hooks/useSessions.ts` (line A: archive/delete/search around `:574-594`; line B: artifact refresh `fetchAndSyncSessionArtifacts` at `:199`), plus both need locale keys. Suggested: enumerate per-line ownership before dispatch, explicitly assigning `index.tsx`, `useSessions.ts`, `index.module.less`, `zh/en.json` to one lane or Codex.

**P2-3 — Quick-card scenario visibility decision unrecorded.** `WelcomeScreen.tsx` renders expert quick cards only on the office scenario (`isDefaultScenario`); the upstream baseline always showed them. The prior review asked for intent confirmation; neither the matrix nor the design doc records this behavior change. One sentence in the boundary section suffices.

**P2-4 — Optional sharpening of the PWA note.** The dock overview's logo fallback is the old Octop `pwa-192.png` (`ChatDockOverview.tsx:52-56`), and no `maskable` icon exists. The `:66` caveat covers install compatibility; adding these two specifics would make the residual fully auditable.

## Residual unknowns

- Codex's executed evidence (66 targeted cases, `make all` 3694 passed, 0 lint errors, build passes) is assessed as **claimed evidence only** per task instructions; I did not and could not rerun it.
- Logo binary validity, real browser rendering/first-paint, actual desktop three-column layout vs WorkBuddy, and full zh↔en key-tree parity beyond the keys touched remain unobservable in this lane.
- Test red-first history is unverifiable without execution logs.

## Repository state & tooling constraints

- **Zero writes performed.** Every tool call was read-only (`read_file`, `glob`, `grep_search`). Nothing was edited, staged, committed, pushed, or executed; no subagents were spawned; no credentials or paid services were touched. The three pre-existing Prettier-modified files noted in the snapshot (`useKeyboardOffset.test.ts`, `chatInputCore.partial.less`, `chatThemeOverrides.partial.less`) were ignored as instructed.
- **Limitation:** this Qwen harness exposes no shell/command tool, so `git status`/`git diff` could not be run to *demonstrate* the unchanged state (unchanged by construction, disclosed), tests/lint/build could not be executed, and the Codex `channel.py` phase-update script could not be invoked — this report is delivered in-band, mirroring the constraint already documented in `docs/xiongbao/GLM_READONLY_REVIEW.md` §0/§6.

**Bottom line:** the parity matrix is fit to serve as the acceptance ledger once the two P1 disclosures (quick-card auto-send, install-prompt copy) and the broken `PHASE2_TASKS.md:25` link are corrected — none require code changes in this phase; they require one- or two-line doc amendments (or deferring the prompt rebrand to phase 2's brand-scan item).

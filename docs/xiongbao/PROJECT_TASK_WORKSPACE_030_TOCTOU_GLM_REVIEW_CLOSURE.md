All verification is complete. The review is done — here is the closure report.

---

# 030A TOCTOU design closure review (read-only, second-pass P2s)

**Fixed SHA:** `d823e3be0f9e660fab61295a78e82068a94990e2` — clean detached-HEAD worktree; session git snapshot shows tip `d823e3be docs(030): close second TOCTOU design review gaps`, matching the fixed SHA at short-hash granularity.

## Verdict

**GO for bounded implementation with `PROJECT_TASK_FILES_MODE_ENABLED = False`** (flag verified `False` at `src/octop/infra/projects/file_tasks.py:22`). **item004 can start from these exact bytes**, conditional only on the worker first executing IT-1/IT-2 as the design's own hard precondition (`PROJECT_TASK_WORKSPACE_030_TOCTOU_DESIGN.md` §6.2, §8.1 step 1). Both P2s from `PROJECT_TASK_WORKSPACE_030_TOCTOU_GLM_REVIEW_SECOND.md` are closed in the fixed bytes; I found **no new P0/P1/P2**. Activation remains a separate **NO-GO** (below).

## Source and tool access limits

- **No shell/Git tool in this session** (plan-mode, read-only): I could not execute `git show a2462c04..d823e3be`, so the delta was verified at **content level only** — the second review's two P2 findings vs. the current fixed bytes — not as a byte-level diff. The clean detached-HEAD snapshot is consistent with the fixed SHA.
- **No installed `deepagents`/`harness-agent` source readable** from this worktree. All `[CODEX-DEP]` facts (six-tool schema at `deepagents/middleware/filesystem.py:1102-1204`, `FilesystemBackend` resolve-first, partial POSIX `O_NOFOLLOW`) remain **Codex-supplied evidence I could not inspect**. My own tool-surface evidence is the repo test only.
- **No tests were run** in this review; no provider calls; no nested agents; no channel events published (`channel.py` requires a shell I do not have). No repository file was modified.

## Q1 — S4 tool-path contract (second-review P2-1): CLOSED

Design §5.2 S4 now states the two-form contract explicitly: single leading `/` strips to an in-root fragment; **bare-relative fragments map directly in-root**; `/` alone is a dir/search root; omitted optional `glob/grep.path` maps to root; `glob.pattern`'s leading `/` stays a virtual search-root anchor; `grep.pattern` is content text, never path-checked; pattern fields never enter the file-path normalizer. The reject list (double leading separators, backslash/UNC, drives incl. drive-relative `C:foo`, `file://`, `~`, standalone `..` components incl. in `glob.pattern`/`grep.glob`, NUL, missing required path, unknown fields/wrong types) applies to the whole tool contract, and **"两种合法路径形态均执行同一 S1/S2/S3 检查”** — identical checks on both forms.

- **Verified against the repo's own evidence:** `test_real_harness_graph_allows_allowlisted_forged_call` (`tests/unit/agents/test_project_task_file_boundary.py` ≈:338-365; design cites :333-364/:347 — minor line drift, substance verbatim) drives the real harness graph with `write_file` args `{"file_path": "forged-positive.txt", ...}` — **no leading slash** — and asserts a non-error ToolMessage plus the file written at the root. The design correctly demotes the schema text to observation (“不能把 schema 文字描述误作运行时强制单前导 `/`") and §2.1 P1 now says both forms are 已观察的合法形态.
- **Positive real-harness obligation per form:** RD-4 requires "**合法单前导 `/`、裸相对片段、`glob/grep.path=None`、根锚定 glob pattern 必须为正例**" in real-harness-constructed integration cases (no live provider); IT-1 mandates asserting both forms plus optional base paths on actually constructed tools; §9.3 forbids fake fail-closed (“不能以拒绝所有绝对路径、所有相对路径或所有缺失路径冒充 fail-closed”). Also verified: `normalize_project_task_io_path` rejects leading `/` (`src/octop/infra/backend/project_task_file_paths.py:48-50`), so the design's warning not to reuse it for tool args is well-founded.

## Q2 — S1/S5/S6 ancestor chain (second-review P2-2): CLOSED

- **S1** (§5.2): **every** HTTP/tool operation, **before `root.resolve()`**, lstat-walks the **entire lexical absolute chain from `root.anchor`** to the runtime root — explicitly 必含 `layout.root`（`OCTOP_HOME` 或 `~/.octop`）、`project-task-files`、`<agent_id>`，“不能只检查最后两级”; running root **or any managed ancestor** replaced must be refused at the next operation's first check. Relative `OCTOP_HOME`/relative root fail-closed without `resolve()`.
- **S5**: validates **before mutation and after creation** (“建立前及建成后”), checks each newly created ancestor immediately, validates `exist_ok` hits the same way, adds the POSIX `O_DIRECTORY|O_NOFOLLOW` probe (new `infra/utils/safe_dirs.py` — correct: `utils` must not import `projects` per AGENTS.md §5; `asset_storage.py` untouched). Failure type pinned to an `OSError` subclass — verified against the create-flow catch (`manager.py` `except OSError` at ≈:833-843).
- **S6**: `_verify_project_task_runtime` gains the same anchor→root chain check (fail → False → existing compensation). Verified the function exists at `manager.py:989-1073` and today only compares `Path(cwd).resolve() != root.resolve()` — the gap S6 closes is real; likewise the `exist_ok=True` junction gap in `ensure_project_task_file_runtime_dir` (`src/octop/infra/utils/paths.py:122-129`) is real.
- **A16** (new row, §6.3): running managed-ancestor replacement — `layout.root` **or** `project-task-files` as a stable junction/symlink with the leaf still a real dir → 403i/TM-err on **HTTP and the six tools** (read/write/ls/glob), zero canary, zero outside side effects, both platforms (Windows junction fail-loud), plus post-restoration positive. Mirrored in RD-2 (HTTP), RD-4 (tools), RD-5 (creation/startup variants).
- **Honesty:** relative `OCTOP_HOME` fail-closed (S1/S5/C3/RD-5, “不能先 `resolve()` 隐藏祖先链接”); parent permissions handled in §2.1 P5 (umask parent enumerable; S5 applies 0700 only to the feature-owned `project-task-files` parent and leaf, never the shared `layout.root`; Windows ACL `[UNVERIFIED]`); N3a keeps activation NO-GO if other accounts can write the managed chain.

## Q3 — residual race posture: intact

§7.2 keeps the split exactly as demanded: only IT-1-confirmed POSIX final-component `O_NOFOLLOW` opens may gate on ELOOP; POSIX parent swaps and all Windows swaps are **non-gating activation-risk evidence** with an explicit ban on claiming "B+ 前置拒绝” catches a post-check swap; the Windows method-entry hook is disclaimed as not an OS-open-time proof; stable plants (RD-1..RD-7 incl. A15/A16) remain the zero-canary gates; observed escapes must be kept as failure samples. §7.3 ST-1 is record-only, never a ship gate. N1/N2/N3/N3a all stand in §2.2, including the real-directory rename residual and the “若实际部署允许其他账户在受管目录链写入，则激活继续 NO-GO” clause; C3 keeps the “在文件系统保持稳定的前提下” qualifier.

## Q4 — item004 readiness: sufficient

The high-level claims now match the executable contract: C2 ↔ S4 two-form rule; C3 ↔ S1 full-chain walk; C6 ↔ S5/S6. §6.1 seams (files, function signatures, boundary compliance), §6.2 IT-1/IT-2, §6.3 A1-A16 with per-platform expectations and positives, §7.1 RD-1..RD-7 anchored to existing fixtures, §7.2 hook discipline, §8.1 rollout order, §8.2 `git revert` rollback, §8.3 fail-closed platforms — all concrete enough to dispatch a bounded single-slice worker. Verified seam facts: `apply_project_task_file_boundary` already holds `root_dir` and constructs the guard (`src/octop/infra/agents/project_task_file_boundary.py`, ≈:146-180), so S4's root injection is a one-line construction change; the glob `("**/*.md","*.md")`→`ws.als(".")` shortcut the S3 seam must cover exists at `src/octop/api/routers/workspace.py` ≈:590-601.

## Findings

**P0: none. P1: none. P2: none.**

**P3 refinements** (pin in the dispatch brief or next doc touch; none block item004):

1. **Unpinned S4 normalization shapes:** leading `./`, interior duplicate separators, trailing slash, whitespace, `.`-only paths are unclassified beyond the two pinned forms. pathlib collapses `.`/empty components and the S1 per-component walk handles them, so there is no bypass (`..`/backslash/UNC/drives all rejected) — the risk is only fail-closed misrefusal of calls like `read_file("./notes.txt")`. Pin normalize-or-reject in S4 + one IT-1 assertion + one RD-4 case.
2. **Availability tradeoff of the full-chain requirement:** a legitimately symlinked ancestor of `OCTOP_HOME` (automounted home, symlinked data volume) makes the feature fail closed and unusable, while §8.2 names only IT-1 schema drift as the realistic misrefusal source. One sentence next to N5/§8.2 stating the deployment constraint (plain-directory chain) keeps operators honest.
3. **Minor citation drift in the doc:** the boundary test cited at :333-364/:347 actually sits ≈:338-365 (args line :347-348). Substance verified verbatim; fix ranges opportunistically.

**Dispatch notes (explicit design decision points, not defects):** settle S7 (`st_nlink>1`, default on, specced either way in A9/RD-1) and the §8.3 unsupported-platform error code (design leans `PROJECT_TASK_FILES_UNSUPPORTED`) in the item004 brief so the worker does not guess.

## Activation: separate NO-GO (unchanged, correctly so)

File-mode activation stays **NO-GO** pending: owner's recorded acceptance of the B+ residual-race posture as the contract's “禁止不安全入口” remedy — including the remotely triggerable P4 fact that ordinary same-account Agents' shell subprocesses can plant/swap reparse points; real PostgreSQL quota concurrency; the authenticated browser create→read/write→restart→revoke journey; real-provider model-visible tool boundary; the full Windows reparse matrix (volume mount points, GUID volume paths, privileged file symlinks, drvfs/9p); and WorkBuddy UI acceptance. Even a perfect B+ leaves N1/N2 (Windows use-time absent; POSIX parents/listings check-time only), acceptable only via owner acceptance or the A-TOOL path (custom backend/upstream change — itself NO-GO per task rules).

**I ran no tests and assert no test results; all verification above is file inspection at the fixed SHA.** Codex should independently verify the changed bytes, the delta scope, and the test suite after the bounded implementation.

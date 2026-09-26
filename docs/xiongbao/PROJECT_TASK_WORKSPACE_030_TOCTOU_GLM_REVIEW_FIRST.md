All verification passes are complete. I have everything needed to render the design verdict. Compiling the final report.

---

# Read-only adversarial review — 030A file-operation TOCTOU activation posture

**Fixed SHA:** `43fb1ac04dd86078d7a8a19492b9eb723d034fd0` (base `f56c33472074bd1399cd94a3e5bc867ed8ca22e6`)
**Verdict: `repair design`** — the B+ strategy selection is sound and honestly scoped, the threat model holds under adversarial challenge, and the activation posture is correctly NO-GO; but two P2 specification defects contradict the design's own guarantee claims and must be repaired in the document before any implementation slice is dispatched. After those repairs, this becomes **GO for bounded implementation with `PROJECT_TASK_FILES_MODE_ENABLED = False`** (unchanged, `src/octop/infra/projects/file_tasks.py:22`).

Separately, as required:
- **Code may be developed with the switch OFF: YES** (after the two P2 repairs; bounded to the §6.1 seams; ordinary-Agent impact verifiably zero — see §7).
- **File-mode activation: NO-GO** (expected and correct — see §8).

## 0. Workspace confirmation and tool-access limits

- This route has **no shell/Git tool** (read-only file tools only) and runs under plan-mode read-only constraints. I could not run `git`, enumerate the base→fixed diff hunk-by-hunk, run tests, or read an installed `deepagents` source (no `.venv` in this worktree; verified by glob). The Codex coordination channel (`channel.py event/ask`) was therefore **not usable** — no phase events were published and no question was escalated. The design document itself records the identical limitation for its authoring session (§9.8), which is consistent.
- Workspace/SHA confirmation rests on the harness git snapshot: clean detached HEAD at `43fb1ac0` with parent `f56c3347` in the log — matching the fixed/base SHAs at short-hash granularity. **No mismatch found; full-SHA and hunk-level scope verification was not possible.** The commit message ("docs(030): propose file-operation TOCTOU activation posture") and the doc set are consistent with the design doc being the only change on the base; I rely on that rather than claiming to have diffed it.
- Evidence provenance: everything marked "verified" below is **my own source inspection**. The `FilesystemBackend` behavioral facts (resolve-first, POSIX leaf `O_NOFOLLOW` on *some* methods, high-level list/glob/grep, Windows lacking `O_NOFOLLOW`) are **Codex-supplied context**, correctly labeled `[DEP-TASK]`/`[UNVERIFIED]` by the design with IT-1 as a hard precondition. Prior test-run numbers (Windows 52 passed, junction case 1/1, frontend 103/103, WSL 4495/17, typecheck red) are Codex evidence quoted from the gate record; I ran nothing.

## 1. Design factual accuracy — verified

Every load-bearing `[CODE]` citation I checked is accurate:

- Check-time resolver: `resolve_project_task_workspace_path` = resolve+containment (`src/octop/infra/backend/project_task_file_paths.py:58-78`); strict entry `project_task_file_io_path` rejects `\\`, `//`, mixed UNC, `from_workspace=False` before `workspace_api_path` erases shape (`src/octop/api/common/workspace.py:160-194`).
- HTTP gate: default-deny, owner-only, 11 allowlisted route families, `as_user` refused, OPTIONS passthrough, fail-closed row-lookup (`src/octop/api/middleware/project_task_file_gate.py:96-116, 131-188`). Chat-attachment route `POST /agents/{id}/upload` is not allowlisted → refused (`src/octop/api/routers/uploads.py:59`).
- Tool surface has **zero Octop-side path checks** — the boundary middleware vetoes by name only (`src/octop/infra/agents/project_task_file_boundary.py:104-142`); `wrap_tool_call` runs before the handler (check-time).
- `write_file` tool-arg shape evidence `{"file_path", "content"}` (`tests/unit/agents/test_project_task_file_boundary.py:345-347`); deepagents 0.7.9 pinned (`uv.lock:857-876`).
- Route traces: tree `:193-215`, file GET/PUT with 403-before-404 ordering, upload resolves the client-controlled filename **before** buffering bytes (`:388-400`), download host-absolute branch gated on `root is None` (`:421-430`), doc GET/PUT root-not-modifiable, preview rewrites source to a proven relative fragment and the cross-agent URL is refused (`:527-542`; verified `resolve_preview_payload` host fallback is unreachable for relative sources, `src/octop/infra/gateway/media/backend_files.py:412-460`), glob pattern through the strict resolver with the repo's own "no containment proof inside the backend glob itself" comment (`:580-585`), mkdir/delete/move/archive refused via `_refuse_if_internal_runtime` (`:45-56, 300, 331, 361, 643, 669`).
- Lifecycle: root creation `mkdir(parents=True, exist_ok=True, 0o700)`+chmod with **no plainness proof** (`src/octop/infra/utils/paths.py:123-129`) — the `exist_ok` junction/symlink-replacement gap is real, and `assert_project_task_file_workspace` compares two resolve()-through-the-link values so it is fooled too (`api/common/workspace.py:142-158`); startup verification (`manager.py:989-1073`), compensation with rmtree (`:1076-1120`), HTTP fallback workspace (`:3287-3297`), create-flow OSError→compensation (`:833-843`), boot re-ensure (`:944`).
- Precedent primitives all exist as claimed: `_ensure_private_dir` (POSIX `O_DIRECTORY|O_NOFOLLOW` probe + fstat + fchmod; Windows lstat/junction refusal), `_open_posix_object_no_follow` (dir_fd walk), `open_regular_file_no_follow` (lstat→open→fstat `(st_dev, st_ino)` identity), `open_download` final-component regular-file check (`src/octop/infra/projects/asset_storage.py:108-183, 281-302`).
- Tests: unit backend matrix and the **POSIX host-absolute canary fallback that motivates the Octop resolver** (`tests/unit/backend/test_project_task_file_paths.py:151-257`); authenticated junction HTTP case with fail-loud `_require_windows_junction`, direction proof, read/download/preview/tree/glob/write refusals, zero outside bytes/side effects, non-following cleanup (`tests/integration/test_project_task_files_workspace.py:348-462`).
- Resolver is internal-only (all call sites behind `root is not None` — verified by grep), so S1-in-resolver cannot touch ordinary Agents.

## 2. Threat-model challenge — holds, with one nuance to state explicitly

I tried to break the central claim ("only P4 can create reparse points"):

- **P1 (model):** the six tools (`ls/read_file/write_file/edit_file/glob/grep`) have no link-creation surface; no shell/execute (boundary veto at execution entry + `tools_disabled` + post-start verification); `edit_file` is read-modify-write of regular files. **Cannot plant.** ✓
- **P2 (owner HTTP):** allowlisted routes write regular bytes only; mkdir/move/delete/archive refused for internal runtimes; upload filename goes through the strict resolver before buffering; chat turn precheck refuses attachments/picks/overrides and requires a bound thread (`chat/turn.py:231-304`); chat WS is owner-only before `accept()` with thread ownership on subscribe (`chat/ws.py:62-115`). **Cannot plant.** ✓
- **P3 (other users/admin/as_user/share readers):** default-deny gate + owner-only assertions everywhere. ✓
- **P5 (other OS accounts):** blocked from planting — leaf roots are 0700 and the parent has no write bit for them — but see P3-5 below (parent dir is umask-default, so runtime ids are enumerable; no read/write though).

**Nuance the design should state in P4's row:** "same-OS-account processes" is not only deployer-local processes — it **includes ordinary Octop agents' shell/execute subprocesses** (same server, same account, potentially prompt-injected). This does not change the verdict: such a shell already has full same-account filesystem access and can read the victim root *directly*, so TOCTOU yields it no privilege gain — exactly the design's honest "no privilege gain for P4" argument, and the contract's exclusion (“OS 上同账户的其他进程访问不属于 030A 所声称的隔离”， contract access/tool-boundary section) covers it by text. But the design's P4 row should name this actor explicitly, because it is *reachable remotely* (a user with an ordinary shell-enabled agent), unlike a "local deployer process" — the residual-race acceptance decision (activation condition (a)) should be made knowing that.

**Contract exclusion of the same-account actor: verified real.** The contract text explicitly excludes it, and offers two TOCTOU remedies — "no-follow 打开或禁止不安全入口". B+ implements the "refuse unsafe entries" branch; that reading is defensible, but since the alternative reading (use-time no-follow) exists, the design is right to make owner ratification an explicit activation condition rather than assuming it.

## 3. Operation-by-operation security judgment (B+ as designed)

| Operation | Today (verified) | B+ adds | Residual (declared, P4-only) |
|---|---|---|---|
| tool `ls` / HTTP tree | name-veto only (tool) / L1+L2+L3 resolve (HTTP); enumeration no use-time guard | S4+S3: lstat walk + bounded reparse-free subtree proof → TM-err / 403 | walk-time swap enters swapped dir |
| tool/HTTP `read_file`/`download`/`doc`/`preview` | backend resolve; POSIX leaf `O_NOFOLLOW` on *some* methods `[DEP-TASK, UNVERIFIED→IT-1]` | S4+S1/S2 (+S7): planted link/FIFO/hardlink deterministically refused | POSIX parent swap; all Windows swaps |
| tool/HTTP `write_file`/`edit_file`/`upload`/doc PUT | backend resolve; creation path not-existence-independent at leaf (403-before-404 verified) | S4+S1/S2: overwrite-through-link refused, incl. existing outside file (closes GLM P3 #3) | parent swap between check and create (write-through); edit_file has two sequential windows |
| tool/HTTP `glob`/`grep` | backend walk with no internal containment proof (repo's own comment); pattern also resolved on HTTP | S3 subtree proof (covers pattern wildcards); S4 on tools | walk-time + per-file-open swaps |
| root creation / startup | `exist_ok` accepts junction/symlink root silently; resolve-equality fooled (verified) | S5/S6 plainness probes (asset_storage precedent) | post-startup steady-state root swap — **not covered as specced (P2-2)** |
| rmtree compensation | no-follow semantics `[PLATFORM]` | RD-6 deterministic proof | — |
| threads/history/read, chat turn/WS | no independent FS surface; content only via tool results (owner-only) | — | — |

## 4. Findings

**P0: none. P1: none.**

**P2-1 — §7.2 seam-hook assertions contradict B+'s own definition.**
- *Where:* design §7.2 (check→use seam hooks); interacts with N1/N3 (§2.2) and §5.2.
- *Trigger:* implement the hooks as written — the swap fires “在 L3/L5 检查已完成、目标名首次被打开” (or at backend method entry on Windows), i.e. **after every Octop-side check including B+'s S1/S2/S4**. By construction a check-time strategy cannot catch it; only POSIX leaf `O_NOFOLLOW` (where IT-1 confirms coverage) can.
- *Impact:* the blanket assertion “canary 永不出现” fails for POSIX parent-component swaps and all Windows swaps; the section claims swaps are caught “或 fail-closed 拒绝（B+ 覆盖处）”, which B+ cannot do post-check. Implementers will either weaken the tests or misread red suites. This is exactly the mechanism by which negative tests were supposed to *deterministically expose the race* — the plan points at it with wrong expected outcomes.
- *Minimal repair:* rewrite §7.2 as a per-case matrix: POSIX final-component swap → ELOOP assertion (only where IT-1 confirms coverage); POSIX parent swap and all Windows swaps → expected outcome is the **documented escape** (canary served / outside side effect), recorded as activation evidence, explicitly non-gating (or `xfail(strict=True)` so the residual stays visible); keep the zero-canary assertion only for the persistent-plant RD-1..RD-7 cases.

**P2-2 — steady-state root replacement is claimed (C3, §4.2) but no rule, seam, or acceptance row delivers it.**
- *Where:* design §2.2 C3 (“含根本身”)， §4.2 (“根本身被顶替…确定性拒绝”)， vs §5.2 S1 (walks only the relative fragment's literal components), S5/S6 (lifecycle-time only), §6.3 (A10 = creation, A11 = startup window; no steady-state row).
- *Trigger:* P4 (or an ordinary agent's shell subprocess — same account) replaces `~/.octop/project-task-files/<agent_id>` with a junction **while the runtime is running**.
- *Impact (verified in code):* `resolve_project_task_workspace_path` resolves root and target both through the junction → containment passes; `assert_project_task_file_workspace` compares two resolved-through-the-link paths → equal; the backend's stored cwd resolves through it as well. Until the next restart's S6, every HTTP and tool operation reads/writes outside with **zero refusals** — directly contradicting C3/§4.2, and invisible to the acceptance table.
- *Minimal repair:* make S1 explicit — every operation's no-follow walk **begins by proving the root itself is a plain real directory** (one lstat/probe, both surfaces — the tool middleware already receives root per S4); add acceptance row A15 (running runtime, root swapped, read/write/ls/glob on both surfaces → refused, zero outside bytes, root not followed) and mirror it in RD-2/RD-4.

**P3 findings (pin during repair; none change the strategy):**
1. **S5 failure plumbing:** `manager.py:833-843` catches only `OSError`; the natural precedent (`_ensure_private_dir`) raises `AssetStorageError`. As specced ("失败 ⇒ 抛错 ⇒ 创建流程走既有补偿”)， a non-OSError raise escapes without compensation → 500 + orphaned row until the 24 h boot scan; A10's "AGENT_FAILED + compensation" would not hold. Pin `assert_plain_directory` to raise `OSError` (e.g. `NotADirectoryError`) or widen the catch at both call sites (`:833`, `:944`).
2. **S1 ENOENT semantics:** creation ops need "first missing component ends the walk; deeper components are check-time unplantable" — required by A3/A8 positives and interacts with IT-1(c) (does `write` create parents). Unspecified today.
3. **Windows junction detection precision:** §5.2 S3 leads with `entry.is_symlink(follow_symlinks=False)` — not a real API (`DirEntry.is_symlink()` takes no kwarg) and, on modern CPython, `is_symlink`-family helpers return **False for junctions** (bpo-37834 lineage). An implementer following the text literally would miss junctions in listing scans on Windows. Mandate `st_reparse_tag != 0` (or `is_junction`) as the primary Windows detector in S1 *and* S3; IT-2 must pin `DirEntry`/`stat` reparse semantics.
4. **Glob fast path bypass:** `routers/workspace.py:590-601` short-circuits `("**/*.md","*.md") and root=="."` to `ws.als(".")`, bypassing `aglob`; the S3 seam must be applied before both branches.
5. **Parent-directory permissions:** `Path.mkdir(parents=True, mode=0o700)` creates `~/.octop/project-task-files/` (and any missing parents) with umask-default (typically 0755), not 0700 — other OS accounts can enumerate runtime ids (leaves are 0700, so no read/write; P5 still cannot plant). The design's §2.1 P5 row cites 0700 without this nuance. Optional one-line parent chmod in the S5 slice — flag as a decision for Codex.
6. **Windows path-shape oddities** (reserved device names `CON`/`NUL`, trailing dots/spaces) are not in the normalize rejection set; mostly correctness/DoS, and S2's regular-file check would refuse device-shaped entries — note for IT-2.
7. Cosmetic: "11 类路由” is 13 (method, path) combos across 11 route families; §5.2 S7 is listed "default on" with a Codex decision point — keep that decision explicit (I concur with default-on: no allowed entry can produce `nlink>1`, so the false-positive surface is zero).

Carried-over, out-of-scope residuals from the prior review remain open and unaffected: backend class-name check (P3 #2), front-end `my:file.txt` vs `/my:file.txt` (P3 #4), typecheck/Chat baseline red (P3 #5).

## 5. Cross-platform limitations (must accompany any activation claim)

- **Windows:** no `O_NOFOLLOW`, `os.supports_dir_fd` empty → no use-time open protection under B+ (N1); handle-bound enumeration would need `NtQueryDirectoryFile`-level NT APIs — correctly declared out of bounded scope; junction detection available via `st_reparse_tag`/`is_junction` (pin via IT-2); `rmtree` junction semantics (3.8+) is `[PLATFORM]`, verify via IT-2.
- **POSIX:** leaf-only `O_NOFOLLOW`, per-method coverage `[UNVERIFIED → IT-1]`; parents, `ls/glob/grep` walks unprotected at use time (N2); `/proc/self/fd` handle re-opening is Linux-only — the design correctly fails closed on non-Linux POSIX.
- **WSL2/drvfs (9p) cross-boundary links, volume mount points, GUID volume paths, OneDrive-style reparse tags** — untested, honestly parked in the independent reparse-matrix gate (N5). Note `st_reparse_tag != 0` will fail closed on cloud-placeholder tags (false refusals if `~/.octop` lives under such sync — availability-only, acceptable direction).
- macOS not in CI → §8.3 fail-closed unsupported branch is the right posture.

## 6. Concrete safe implementation path (after the P2 repairs)

1. Repair the design document (P2-1, P2-2; fold P3-1/2/3/4 pins into §5.2/§6.2/§6.3; add A15).
2. IT-1/IT-2: read the installed `deepagents/backends/filesystem.py` (0.7.9) + harness six-tool schemas in a runnable venv; pin O_NOFOLLOW coverage per method, enumeration/stat follow behavior, parent-creation, edit atomicity, real arg schemas; pin Windows reparse/dir-entry semantics. Any deviation from `[DEP-TASK]` facts → report to Codex, reopen the affected design rows.
3. `infra/utils/safe_dirs.py` + `paths.py` S5 (OSError-raising plainness probe) → resolver S1/S2/S7 (with root-first walk) → HTTP call sites incl. both glob branches → S4 tool middleware (root-injected, fail-closed arg shapes) → S6 verifier → RD-1..RD-7 RED→GREEN, junction cases fail-loud per the repo convention.
4. §7.2 hooks with the repaired per-case expectations; ST-1 stress stays non-gating.
5. Every step inside internal-runtime branches; switch stays `False`; single-slice commits, `git revert` is the full rollback; A14 ordinary-Agent regression suite must stay green.

All seams verified to exist exactly as the design places them; no dependency, spec, migration, or gate-allowlist change is needed for B+ (`_verify_project_task_runtime` and `files_backend_constructible` remain compatible since the backend spec is untouched).

## 7. Activation verdict — NO-GO (explicit)

File mode must remain OFF. Reasons, in order:
1. B+ is unimplemented and untested; a design document is not feature evidence (repo's own standing rule).
2. The owner has not recorded acceptance of the P4 residual race (including the ordinary-agent-shell actor) as the contract's “禁止不安全入口” remedy — the design itself makes this activation condition (a).
3. All §8.4 independent gates remain open: real PostgreSQL migration + quota concurrency; authenticated browser create→read/write→restart→revoke journey; live-provider model-visible tool set and call boundary in production (all current evidence uses fake/recording models — **no successful model exit counts as acceptance**); full OS reparse matrix; WorkBuddy UI acceptance / 25-item matrix / PS-08.
4. Even a perfect B+ leaves N1/N2 (Windows use-time absent; POSIX parents/listings check-time only); that is acceptable *only* through the explicit contract-remedy reading in (2) — or via the A-TOOL path.

**Exact missing primitives for use-time confinement** (the NO-GO record, matching the design's §5.4/§6.5): (i) POSIX per-level `openat(O_NOFOLLOW)` walks anchored to the managed-root fd, injectable at the installed backend's actual open call sites — unobtainable without a custom backend or upstream change; (ii) Windows handle-relative no-follow open and handle-bound enumeration (`NtCreateFile`/`NtQueryDirectoryFile` level) — absent from Python stdlib; (iii) the verified per-method no-follow coverage map (IT-1); (iv) the repaired deterministic race-exposure test matrix (P2-1); (v) the steady-state root-swap mechanism and acceptance row (P2-2).

**Bottom line:** `repair design` — fix the two P2 specification contradictions (and pin the P3s), then dispatch the B+ slice strictly with the switch OFF; file-mode activation stays NO-GO until the owner ratifies the residual-race posture and every independent gate is actually green under dual-platform re-verification.
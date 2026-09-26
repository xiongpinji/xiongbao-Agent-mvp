# 030A B+ 受管文件任务路径加固：关闭开关的集成验收

日期：2026-09-27。集成基线 `94d324603012ecc12ea411f851898cd67edb280a`，GLM 固定审查候选 `63b8cdf22809e06199194e6cdde4ffff20435b0f`，集成代码提交 `136f9e69`。后者的 12 个候选文件与固定审查提交逐字节相同；两棵树的其他差别仅为基线已有的 B1 验收文档和 PostgreSQL 测试 fixture。本文只裁定**文件模式开关关闭时的集成**，不裁定文件模式激活或 WorkBuddy 对标完成。

## 裁决

- **关闭文件模式的集成：GO。** `src/octop/infra/projects/file_tasks.py` 的 `PROJECT_TASK_FILES_MODE_ENABLED = False` 保持不变，创建入口仍返回 422。GLM 5.3 在固定 `63b8cdf2` 上只读续审给出 gated integration GO，所检查范围内无新增 P0/P1/P2；Codex 核实集成提交的 12 个文件与该候选字节相同，并完成下述独立测试。
- **开启文件模式：NO-GO。** Windows 运行中根/祖先目录替换的三个测试因后端持有目录句柄而在 `PermissionError` 时明确跳过，它们是缺失的激活证据。同账号 check→use 竞态处置、完整 Windows reparse 矩阵、真实已认证 HTTP/浏览器与模型工具回合、PostgreSQL 实库迁移和并发仍未闭环。不得把稳定植入拒绝或绿色聚焦测试写成 OS 沙箱保证。

## 本批实现与审查

本批在 HTTP 私有文件入口、六个受管文件工具和运行时根目录生命周期上增加 S1/S2/S3/S5/S6/S7 的 no-follow 与有界 fail-closed 检查：拒绝稳定植入的符号链接、Windows junction/其他 reparse 点、硬链接和特殊文件，列表/搜索在发现不安全子树时整体拒绝。普通 Agent 仍走原分支。Codex 对未完成的 030A B+ 候选定点修复了 Windows `DirEntry.stat` 硬链接计数、四处重复 junction 测试建目录、三个 Windows live-rename 测试的明确跳过条件，以及含 junction 根列表应返回 403 的旧断言。此次代码提交覆盖 12 个路径；原两条实现路由的失败记录未改写为成功。

Agent Orchestrator 只读审查保留两条记录：`qwen-code-review-20260926-212550-4780d6` 读完生产侧模块后触及客户端 16 轮上限，**没有裁决**；`qwen-code-review-20260926-213615-c6c280` 对同一固定 SHA 做窄范围续审，结论为“gated integration GO / activation NO-GO”，并明确未自行执行 shell、Git diff 或测试。Codex 在 Orchestrator 记入 `accepted` 的对象是第二条成功审查；测试数字由 Codex 独立取得，而非 GLM 声称。

## 独立验证

| 环境与范围 | 结果 |
| --- | --- |
| 原生 Windows，五个 030A 聚焦套件，固定候选 | 243 通过、32 跳过、0 失败；其中三个 live-rename 跳过属于未验证激活证据 |
| Linux ext4，五个聚焦套件，固定候选 | 263 通过、12 跳过、0 失败 |
| Linux ext4，五个聚焦套件，集成树 | 263 通过、12 跳过、0 失败 |
| Linux ext4，`tests/unit tests/integration -m 'not live and not postgresql'`，固定候选 | 4,647 通过、15 跳过、0 失败 |
| 同一完整回归，集成树 | 4,647 通过、15 跳过、0 失败 |
| 集成树，`ruff check src tests` 与 `ruff format --check src tests` | 均通过；1,119 个文件符合 Ruff 格式 |
| 集成树，`mypy src/octop` 与 `git diff --check` | 542 个源文件无 mypy 问题；补丁无空白错误 |

本批未改 dashboard 源码，未运行前端构建。仓库的 `make all` 会先对整个仓库执行写入式 `format-all`，因此没有把它作为此脏的并行工作区的提交钩子；上表是独立运行的非写入检查与限定的后端回归。WSL 提交时现有 hook 的 CRLF shebang 报错（`env: 'bash\r': No such file or directory`），仅对该集成提交临时关闭 hook；不能把 hook 记作通过。

## 激活前仍需的证据

在开关保持关闭的前提下，下一批要用 Windows 实机可释放句柄的 staging 重跑三条根/祖先替换路径，覆盖卷挂载点/GUID 路径等 reparse 类别，并完成真实登录下的创建、读写、预览、下载、重启和撤权；随后用真实 provider 验证六工具可见性及越权拒绝，以 PostgreSQL 实库验证迁移、配额和并发。对同账号 TOCTOU 需另有设计决定与可执行门禁。上述项未完成时，文件模式不得激活，PS-08 不核销。

# 030A 下一批安全与前端门禁记录

状态：固定代码 `4a2efb55b1f60e7d92d34a73f01f60da15771cb0` 经 GLM 只读复核 **GO for gated integration，无 P0/P1/P2**，已推送 `xiongbao/main` 并核对远端 SHA。**只允许创建开关关闭时集成，不允许激活文件模式。**`PROJECT_TASK_FILES_MODE_ENABLED = False`，受控文件任务创建仍关闭。此前 [F3 集成候选](PROJECT_TASK_WORKSPACE_030_F3_INTEGRATED_ACCEPTANCE.md)的未达项仍适用。

本批只处理三处门禁：Windows 真实 NTFS junction 的已认证 HTTP 逃逸回归；B2 内部运行体启动后的配额、项目指令和六工具边界中间件核对；私有 dock 中安全/拒绝路径标签冲突、跨任务标签清理和失去授权后的陈旧文件预览。普通 Agent 文件路径请求分支没有改为私有任务规则。涉及 13 个受控代码及测试文件，无开关、依赖、迁移或生成物变更。

## Codex 独立验证

- 在固定代码的 Windows 集成工作树运行两组相关后端 pytest：**52 通过、2 个平台条件跳过**；单独运行真实 junction 的已认证 HTTP 用例：**1/1 通过**。该用例验证现存外部文件的读、下载、预览、树和 glob，以及不存在外部目标的写入拒绝；它不是对所有重解析点或竞态攻击的证明。
- Windows 前端私有 dock、普通路径与文件面板 5 组 Vitest：**103/103**；`npx tsc -b`、目标 ESLint、Vite/PWA `npm run build` 通过。候选工作树先前扩展到 12 组相邻套件 **178/178**。普通 Chat 全量套件的两处失败已在未改的 `198f5cf9` 基线重现，不能写成当前全绿。
- 后端候选 WSL 非 live 全量 pytest：**4495 通过、17 跳过**；`make lint` 通过。固定集成代码的目标 Ruff check/format 和 `git diff --check` 通过。`make typecheck` 的两个 `unused-ignore` 错误位于未改的 `asset_storage.py`，仍失败；`make all` 未运行，不能宣称通过。
- OpenCode 第三次运行 exit 0 但只读了状态和部分 diff，读取工作区外审查记录遭自动权限审查拒绝，没有改代码、测试或最终报告。前端候选由前两轮实现，Codex 按实际字节和独立测试接受；不能把第三次 `succeeded` 当作实现或 TDD 证据。

## 固定 SHA 只读复核

`qwen-code-review-20260926-135410-f31355` 在干净、分离的 `4a2efb55` 工作树检查 Windows junction 已认证 HTTP 测试、私有 dock 与普通 Agent 兼容、B2 启动后中间件核对和失败补偿，给出开关关闭时可集成结论。GLM 没有 shell/Git 工具，未运行测试，也未能逐 hunk 枚举 diff；Codex 独立确认固定提交对 `198f5cf9` 恰有上述 13 个受控文件，`git diff --check` 通过。

GLM 留下五项 P3：路径校验与打开之间的 TOCTOU 窗口（**激活阻断项**）、仅按后端类名校验的理论风险、junction 已存外部文件覆盖写入测试可补强、`my:file.txt` 与 `/my:file.txt` 的前端规则不一致，以及既有 typecheck/Chat 测试失败使根 ship bar 未全绿。这些不阻断创建开关关闭时的代码集成，均不能解释为文件模式已达上线条件。完整结论及逐项证据见 [GLM 只读审查原文](PROJECT_TASK_WORKSPACE_030_NEXT_GLM_REVIEW.md)；Codex 对其局限已在本记录中单独说明。

## 保持关闭的门槛

还需真实 PostgreSQL 迁移和配额并发、已认证创建—读写—重启—撤权的浏览器旅程、实际模型/运行体工具可见性与调用、OS 级重解析点和 TOCTOU 风险处置。Windows 本次 HTTP 用例只补一个实际 junction 场景。WorkBuddy 的既有目录选择、真实本地命令、云端/团队协作和逐状态视觉 1:1 均是后续工作；本批不核销总体 25 项矩阵或 PS-08。

代码提交 `4a2efb55` 已推送并用 `git ls-remote xiongbao refs/heads/main` 核对；本记录单独随文档提交推送。

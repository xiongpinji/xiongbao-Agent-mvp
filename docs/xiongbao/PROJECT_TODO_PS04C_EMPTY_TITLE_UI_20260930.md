# PS-04C 新建待办空标题按钮补验（2026-09-30）

范围仅为新建待办弹窗的「创建」按钮状态。既有 [WorkBuddy 5.6.2 实机观察](PROJECT_TODO_PS04C_WORKBUDDY_OBSERVATION.md)记录：标题为空时「创建」不可用。熊宝先前可点击该按钮，再由提交函数提示标题必填；修复提交 `ab87872e0df2bc0c73e56a28ed1e4fb4d2d7c2b2` 让按钮直接复用已存在的 `trimmedTitle.length > 0` 判断。编辑态的未修改禁用规则和提交前校验保留。

| 门禁 | 本次实际结果 |
| --- | --- |
| TDD 红灯 | 新测试先运行，1 失败、37 跳过；空标题时 `toBeDisabled()` 失败，收到的是可点击按钮。 |
| TDD 绿灯 | 最小改动后，聚焦新测试 1/1；整份 `ProjectPlan.test.tsx` 38/38。测试覆盖初始空标题、纯空格、填写有效标题、再次清空和未发送创建请求。 |
| 静态与构建 | 两个改动文件 Prettier 和 ESLint 通过；全前端 ESLint exit 0（67 条既有 warning）；`npm run build` 的 TypeScript 与 Vite exit 0。后端 `ruff check`、`ruff format --check`、mypy 分别 exit 0。 |
| 独立审查 | 对两文件候选差异的只读代码审查未发现 P0–P3 问题。暂存差异为 2 文件、22 行增加、1 行删除，`git diff --cached --check` 通过。 |
| 发布 | 仅两文件提交至 `xiongbao/main`；提交后远端 SHA 与 `ab87872e` 一致，工作树干净。 |

默认 Git pre-commit 在此 Windows 主机因缺少 `make` 未启动成功。使用临时、限定两文件的提交钩子实际重跑目标 Prettier、ESLint、整份 38 项测试和生产构建，全部 exit 0；仓库永久 `.githooks` 和 `core.hooksPath` 未改。全仓 `npm run format:check` 报 1073 个文件不合格式，其中本次两个改动文件单独检查通过；未对无关文件执行写入式格式化。`pytest --testmon` 的实际选择范围过大，在约 19 分钟后仅到 1%，人工中断，**不计通过**；其相关 Python 进程已退出。

本次没有新的 WorkBuddy 同夹具操作、真实 TCP/浏览器视觉对照、GLM 固定 SHA 复审或 Windows 安装包验收。既有 [C1/C2 本地验收](PROJECT_TODO_PS04C2_ACCEPTANCE_20260929.md)仍不等于 PS-04 完整旅程或项目空间 11 条 1:1 双验收。

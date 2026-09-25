# 030A F1 前端隔离候选验收

状态：**候选通过；未接入主线，未开放文件任务**。OpenCode/DeepSeek 的首轮前端任务超时，留下 16 个文件的候选差异；Codex 在超时后单独记录了语法和隐私边界修复。Claude/Qwen 的两轮定向修复补齐私有任务首次渲染资源清空、深链切换，以及文件能力消失时禁止无意创建普通对话任务。2026-09-25 Codex 审查完整 16 文件差异，并将第三轮任务 `claude-bailian-20260925-133628-781c3b` 在编排器中记为**隔离候选通过**。该结论不归功于单个执行器，也不代表后端或真实浏览器功能完成。

独立 WSL 验证：7 个聚焦 Vitest 文件 **107/107 通过**，`npx tsc -b`、16 文件 Prettier、`git diff --check`、`npm run build` 通过；目标 ESLint **0 错误、2 条已有 React Hook 依赖警告**。测试输出另有 jsdom `getComputedStyle` 与 React `act` 警告，没有失败断言。未运行真实账号/浏览器文件任务、Windows 安装包或 PostgreSQL；B3/B4 服务端 API 和默认拒绝门禁仍未实现。

候选实现了前端能力提示、显式文件模式、本人私有运行体深链、普通 Agent 列表过滤、文件任务新建/分叉/管理/浏览器/终端入口限制、与解绑区分的整任务删除入口。`PROJECT_TASK_FILES_*` 的服务端错误只由模拟响应测试。能力变化时已选择的文件模式不会静默降级创建普通对话；需要重新确认。接入前须补 `projects.tasks.createFilesCapabilityLost` 的中英文 locale 文案，并在 B3/B4 后对实际权限与资源隔离做三身份浏览器验收及固定源码只读审查。

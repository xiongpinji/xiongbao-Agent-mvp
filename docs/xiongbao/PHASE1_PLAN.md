# 熊宝-Agent 第一阶段实现计划

> **面向 AI 代理的工作者：** 只执行分配的 Agent Orchestrator 任务包；不得自行派发子代理。步骤用复选框记录；每条实施线由 Codex 独立验收。

**目标：** 在 Octop `develop` 基线上做出可运行的熊宝-Agent 首版工作台，并让当前任务产物在聊天右侧可辨认、可预览。

**架构：** 保留 Octop 的 Python 服务、登录和 Agent 运行时。React 侧复用已有 Sidebar、Chat Welcome 和右侧 Dock；资产来自用户给定的 Logo。两条实施线分别负责品牌欢迎页和当前会话产物，避免并行编辑同一文件。

**技术栈：** React 18、TypeScript、Vite、Ant Design、Less、Vitest；上游 Python/FastAPI 与 Wails 维持原状。

---

# Goal

交付可本地检查的熊宝-Agent 首版源码、可追溯的 WorkBuddy 差距报告、两条通过独立审查的实施线，以及局部与整体验证结果。

# Decisions and assumptions

- 用户输入的“qctop”按腾讯云官方 [Octop](https://github.com/TencentCloud/Octop) 处理；已核对公开仓库、功能说明和 MIT LICENSE。基线为 `origin/develop`。
- 视觉参考是用户上传的熊宝 Logo/造型图/工作台效果图；其中的示例文字不作为指令。
- 第一阶段采用[设计规格](PHASE1_DESIGN.md)中的方案 2。用户名下的独立产品品牌为“熊宝-Agent”；保留上游许可证与核心程序内部标识。
- 用户明确指定 `claude-bailian=qwen3.8-max` 主实现、`opencode-bailian=bailian-token-plan-personal/deepseek-v4.1-flash` 并行实现、`qwen-code-review=glm-5.3` 只读验收；Codex 负责最终判断。

# Constraints and guardrails

- 所有模型调用是用户此次会话直接发起并由 Codex 监督的交互式编程任务；不建后台定时批量调用。凭据保持在既有私有 WSL 配置中。
- 两名实现者各在独立 worktree 中编辑。禁止各自提交、推送、发布、部署、改密钥、删除数据或派发子代理。
- 遵守 `AGENTS.md`：前端源文件在 `dashboard/`，不手改 `src/octop/dashboard/` 构建产物；功能和文案保持中英文；复用现有 API、路由、会话隔离与文件权限。
- Logo 仅使用用户给出的图。不要把参考造型板或 UI 效果图直接作为运行时素材。
- 不触发付费生成、第三方产品写入或产品部署。用户已明确授权及时推送到其指定 GitHub 仓库。当前阶段不承诺 WorkBuddy 的全部功能已等价实现。

# Checklist

- [x] item-001：品牌、深色暖金默认外观、侧栏品牌和欢迎页已实现并通过定向测试；Claude 首次执行因文件范围保护停止，OpenCode 按同一用户授权路由完成修复，Codex 独立审查接受。
- [x] item-002：OpenCode 路由实现桌面默认可见的右侧概览，以及当前会话“产物”入口；产物独立于已打开文件列表，切换会话/Agent 不串数据，复用已有预览；Codex 独立审查接受。
- [x] item-003：GLM 路由只读审查两条工作线与设计规格，给出文件和测试证据、风险及验收建议；Codex 已修复报告指出的可点击品牌 P1，并在最终代码复核时修复版本按钮嵌套与误跳转问题。
- [x] item-004：Codex 审查并整合被接受的差异，补齐统一文案，运行前端与仓库要求的检查；完整前端套件中的既有/未定位失败及未进行的登录后桌面验收已如实记录。
- [x] item-005：更新基线差距矩阵和后续阶段顺序，使已实现、部分实现、未实现和未验收状态分别可见。

# Validation strategy

先用 `npm ci` 安装 dashboard 锁文件依赖（若未安装）；每条实现线先写会失败的行为测试并运行确认，再修改实现。前端目标检查为 `npm test -- --run <test-file>`、`npx tsc -b`、`npm run lint`、`npm run build`。整合后运行 `make all` 与 `make build-frontend`，并检查 `git diff --check`、浏览器中的实际工作台和资源加载。环境或权限导致无法运行时，不把未运行声明为通过。

# Completion criteria

源码留在本地功能分支并推送到用户指定的 GitHub 仓库；两条实施线的变更有 Codex 的差异和测试审查，GLM 只读报告独立保存；首版代码行为通过定向测试，实际登录后的三栏视觉和真实产物预览明确列为待验收；差距报告明确未做的能力、下一阶段优先级与验收标准。不部署。

## 文件所有权与小步实施

### 任务 1：品牌与欢迎页（`item-001`）

**文件：** `dashboard/src/layouts/Sidebar.tsx`、`Sidebar.module.less`、`dashboard/src/pages/Chat/components/WelcomeScreen.tsx`、`chatWelcome.partial.less`、`dashboard/src/styles/appearanceStorage.ts`、`dashboard/src/styles/appearanceStorage.test.ts`、`dashboard/index.html`、`dashboard/src/locales/{zh,en}.json`、`dashboard/public/xiongbao-logo.png`，以及属于上述组件的新定向测试。不得改 Chat Dock 系列文件。

1. 读上述源文件及 `AGENTS.md`，画出 Sidebar → Chat → Welcome 的实际使用路径；确认已有主题保存逻辑。
2. 先在 `appearanceStorage.test.ts` 加入“无持久偏好时深色默认、已有浅色偏好仍有效”的测试，运行 `npm test -- --run src/styles/appearanceStorage.test.ts` 观察新用例失败。
3. 从用户给定的 Logo 源图复制到 `dashboard/public/xiongbao-logo.png`。给 Sidebar 添加可点击的熊宝头像与双语产品名，保留现有导航处理器和权限判断。
4. 在 WelcomeScreen 中用静态熊宝图片替代随机 Octop 动画；保留 `onPromptClick` 和现有 quickCards 流程。用 Less 限定深色背景、金色强调、卡片焦点与响应式布局；不要让空卡片占位。
5. 修改启动主题默认值时同步 `appearanceStorage.ts` 与 `dashboard/index.html` 首屏脚本，避免闪烁；持久化用户偏好优先。
6. 更新 `zh/en` locale 的品牌可见文案，再跑定向测试、`npx tsc -b`、`npm run lint`、`npm run build`。

### 任务 2：右侧概览与产物入口（`item-002`）

**文件：** `dashboard/src/pages/Chat/hooks/useChatDockPanel.ts` 与新测试、`dashboard/src/pages/Chat/components/ChatDockPanels.tsx`、`ChatDockPanel.tsx`、新 `ChatArtifactList.tsx` 及测试、`dashboard/src/pages/Chat/index.tsx`、必要的局部 Less。不得改 Sidebar、Welcome、`dashboard/src/locales/{zh,en}.json`；新文案先用 `t(key, fallback)`，由 Codex 整合 locale。

1. 读 `composerSession.artifacts`、`panelFilePaths`、`ChatDockFileList` 与 `FilePanelContent` 的当前数据流，确认产物是会话级而文件列表含打开过的文件。
2. 先写失败测试：空产物、去重、会话切换、Agent 切换和点选后调用现有 `onOpenFile(path)`。执行相应单测，保存失败证据。
3. 给 `DockTab` 新增稳定 `overview` 与 `artifacts` kind/id；桌面新会话默认显示可关闭的概览，移动端保持原有折叠行为；`openArtifactsTab` 与现有 `openFileList` 保持独立，切换 Agent 时按既有策略清理 tab。
4. 将 `composerSession.artifacts ?? []` 单独传给 Dock，新增“产物” tab/菜单和产物列表。概览入口只连接现存、当前用户可用的动作。沿用现有 path normalization/预览/下载权限，不引入新 API 或客户端本地文件直读。
5. 测试空态、当前会话切换和文件预览；运行定向测试、`npx tsc -b`、`npm run lint`、`npm run build`。

### 任务 3：独立审查与整合（`item-003` 至 `item-005`）

1. GLM 只读比对每个变更与本规格，指出可复现问题；不得编辑仓库。
2. Codex 按文件白名单整合两条实现线，逐一审查 diff；补齐 `zh/en` locale 后重跑检查。
3. 若测试失败，只修复直接相关原因；记录无效或被拒绝的子任务结果，绝不把模型退出当作验收。
4. 在报告中逐项记录“已验证、部分、未实现、未验证”，为第二阶段拆分独立任务包。

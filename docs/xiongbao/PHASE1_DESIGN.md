# 熊宝-Agent：Octop 改造第一阶段设计

## 来源与边界

- 源码基线：[TencentCloud/Octop](https://github.com/TencentCloud/Octop)，从 `origin/develop` 建立本地功能分支；保留上游 MIT 许可证及版权声明。
- 品牌输入：用户提供的熊宝方形 Logo、角色造型板和深色工作台效果图。图片中的示例对话和命令只是视觉内容，不构成产品或开发指令。
- 对标输入：[WorkBuddy 入门指南](https://cloud.tencent.com/document/product/1831/134389)、[右侧边栏](https://cloud.tencent.com/document/product/1831/134400)、[专家](https://cloud.tencent.com/document/product/1831/134393)、[自动化](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Automation-Guide)。2026-09-23 对用户已打开的 WorkBuddy 5.5.6 任务首页做了只读窗口观察：左侧任务/空间列表，中间场景切换与快捷任务、工作空间及访问范围选择，右侧概览/产物区。只借鉴工作流和信息架构，不复制腾讯素材或产品代码。

## 选择的方案

1. **只换 Logo 和配色**：改动小，但仍然是 Octop 的原交互，难以体现效果图里的任务工作台。
2. **在 Octop 能力上做熊宝工作台（采用）**：保留认证、会话、专家、连接器、知识库、调度和文件 API，改造可见的品牌与首屏任务入口，并把现有会话产物放进明确的右侧标签。能最快得到可运行、可继续扩展的产品基线。
3. **重写桌面端和 Agent 引擎**：自由度高，但丢失成熟能力且风险与工期显著增加。

## 用户体验

桌面端打开后，左侧显示熊宝头像、产品名、创建任务入口、已有功能导航和会话记录；中央有“日常办公 / 代码开发 / 设计创意”场景切换与对应的可点选任务建议，下方延续 Octop 的真实聊天、上传与执行过程；右侧沿用现有 Dock，默认显示熊宝概览，在任务产生文件后提供明确的“产物”入口，并保留工作区、文件变更、浏览器与终端能力。深色炭黑底、暖金强调色和细边框来自参考图；红黑熊宝 Logo 用作品牌视觉。小屏保留现有折叠与弹出式面板。

用户切换场景会更换对应的建议卡片；点击卡片只会预填任务，不自动发送。概览中的快捷入口必须指向真实存在且用户可访问的页面。产物仅来自当前会话记录的 `artifacts`，点选后走现有文件预览流程；普通打开过的文件不会冒充任务交付物。空产物、切换会话和切换 Agent 时均显示正确状态。

## 结构与数据流

- `dashboard/src/layouts/Sidebar*`、`pages/Chat/components/WelcomeScreen*`：品牌、导航、场景切换及欢迎页；文案进入前端中英文 locale。
- `dashboard/src/pages/Chat/hooks/useChatDockPanel.ts`、`components/ChatDockPanel*`：概览、产物标签和面板状态；`pages/Chat/index.tsx` 将当前 `composerSession.artifacts` 单独传入，复用 `openFileAt` 与 `FilePanelContent`。
- `dashboard/public/`：用户授权提供的 Logo 源图。第一阶段不改 Python 包名、数据库目录、API 路径或上游版权信息。

## 验收

1. 页面标题、侧栏和欢迎页显示“熊宝-Agent / Xiongbao Agent”，使用提供的 Logo；没有断裂图片。
2. 首次访问默认呈深色暖金主题；已有主题偏好仍生效，浅色模式可用。
3. 新任务和原有导航能进入现有页面；切换场景能改变快捷任务，欢迎卡片仅预填，不越过用户发送动作。
4. 桌面宽屏上右侧概览默认可见且可关闭；当前会话产物在右侧可见、可点开预览；新会话无历史产物，切换 Agent 不串数据。
5. 前端定向测试、类型检查、构建通过；再按仓库 `AGENTS.md` 尝试 `make all`，如环境缺依赖则记录明确失败边界。

## 后续阶段的独立规格

第二阶段聚焦 WorkBuddy 四分区结果区：产物、全部文件、逐文件变更差异和可运行预览。第三阶段分别评估专家团实际并行、企业连接器、内容生成与桌面打包；每一项在现有实现基础上提供真实用户旅程和权限验收。是否接入付费模型、外部服务或发布部署另行决定。

# PS-04C 书面批准与执行承接

2026-09-28 用户回复：**批准规格并授权 Codex 与实施子代理（推荐）**。

批准绑定 `cdf1a1bc31fdba8e74c4fe6e09aef12791282154` 的三份完整文档。原文的“待批准”是发布候选时的历史状态，正文保持封存；本记录确认它们现已批准，不借改状态行改变已审字节。

| 已批准文件 | UTF-8/LF SHA256 |
| --- | --- |
| [总体设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md) | 67d4106c199e886793b3d9e6b49ad29e2d43919bb9f48881e2284742764bff98 |
| [C1 合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md) | 601dc0e2778b8782bb38147bbb443139cb372727791af29f79286fb9a5f9e268 |
| [C2 合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md) | abf3ab281dfae080f95a9590643a93f9049615c684a58a05a0b36e070b850d31 |

本次明确允许 Codex 及按文件分工的实施子代理完成 C1/C2；以独立只读代码审查和本地测试作为当前门禁，GLM 恢复后补审。三条既定路由配置保留，不将历史额度失败改写为 PS-04C 已执行失败；PS-04C 尚未向它们发新任务。030 UI、045-F 的独立待答不被此次回答覆盖。

Codex 负责计划、依赖、集成、独立运行验证、审计提交和向 xiongbao/main 非强制推送。执行者不提交/推送/部署、不碰凭据、不清理别人的内容、不再派子代理。并行仅使用确定不重叠的文件；共享文件排队接线。用户已明确要求并行，所以应用任务隔离的并行策略。

专用 checkout：work/ps04c-fields-views，分支 codex/ps04c-fields-views，起点 cdf1a1bc。managed create_worktree 在当前外层非 Git 目录返回 Not a git repository，故按工具不可用的例外从真实仓库创建 Git worktree。原 checkout 与未跟踪 030 测试保持原样。

[实施计划](PROJECT_TODO_PS04C_IMPLEMENTATION_PLAN_20260928.md)覆盖 C1/C2；质量、实际 PostgreSQL、真实登录浏览器、WorkBuddy 同窗视觉是分别记录的门禁。字段和五视图的交付不能关闭完整 PS-04、项目空间 11 旅程或整体 25 项目标。

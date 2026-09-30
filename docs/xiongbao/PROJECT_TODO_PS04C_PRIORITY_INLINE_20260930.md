# PS-04C 优先级逐项编辑入口补验（2026-09-30）

[WorkBuddy 5.6.2 实机观察](PROJECT_TODO_PS04C_WORKBUDDY_OBSERVATION.md)记录了优先级弹层中每个非空选项旁的「编辑」控件，但没有验证编辑权限、保存后数据或持久化。已批准的 [PS-04C 设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)规定 owner/admin 管理项目目录，成员仅读取、选择；此前熊宝虽有目录管理表单，优先级选择弹层内没有逐项直达入口。

提交 `471096b04d59fcfa8cce365854009e8b830bafb3` 为 owner/admin 的优先级行加入「编辑」。点击后打开既有管理表单，并按所点选项预填名称、颜色和目录修订基线；打开、取消不会选择优先级或保存待办。目录写入仍走原有权限、修订冲突和刷新流程；目录不可用、加载中或页面禁用时，直达按钮禁用。普通成员不显示编辑入口。

| 门禁 | 实际结果 |
| --- | --- |
| TDD | 新集成用例先因找不到「编辑 紧急」失败，接线后通过。它检查预填名称/颜色、待办优先级不变、取消后仍不变；现有成员测试增加无编辑入口断言。 |
| 回归 | `TodoFields.test.tsx`、`TodoCatalogManager.test.tsx`、`ProjectPlan.test.tsx`、`ProjectTodoDetail.test.tsx` 合计 **113/113 通过**；临时提交钩子复跑同组 **113/113 通过**。 |
| 静态与构建 | 四个改动文件 Prettier、三份 TS/TSX 文件 ESLint、`npx tsc -b`、`npm run build` 和暂存差异检查均通过；提交钩子再次构建通过。 |
| 独立审查和推送 | 独立只读审查未发现本次差异问题；仅四个相关文件提交，远端 `xiongbao/main` 已核对为 `471096b0`，代码提交后工作树干净。 |

默认仓库 pre-commit 在此 Windows 主机因缺少 `make` 无法执行；本次使用仓库外、严格限定四个暂存文件的临时提交钩子运行上述目标检查，没有改动永久钩子配置。未重新操作 WorkBuddy，也未做熊宝真实浏览器视觉或保存后持久化对照、GLM 固定 SHA 复审；本记录不核销 PS-04C 或整体 WorkBuddy 1:1。

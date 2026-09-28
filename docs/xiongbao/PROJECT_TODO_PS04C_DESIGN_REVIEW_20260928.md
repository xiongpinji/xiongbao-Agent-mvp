# PS-04C 书面规格独立审查记录

日期：2026-09-28。状态：**三份规格候选已完成独立只读复核，可以提交用户审阅；尚未获实施批准**。源码基线为 `10afd308a222d3e8c95a149a3fdcd699e6d8bc70`，不是本批业务实现 SHA。Codex 是三份文档作者；下列子代理未编辑文档、业务代码或测试，未运行应用、数据库、测试或付费路线。本记录不是 GLM 审查，也不核销日期/目录/五视图或 WorkBuddy 1:1 功能。

## 固定的规格字节

| 文档 | SHA-256（UTF-8 文件原字节） |
| --- | --- |
| [总体设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md) | `67d4106c199e886793b3d9e6b49ad29e2d43919bb9f48881e2284742764bff98` |
| [C1 字段合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md) | `601dc0e2778b8782bb38147bbb443139cb372727791af29f79286fb9a5f9e268` |
| [C2 视图合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md) | `abf3ab281dfae080f95a9590643a93f9049615c684a58a05a0b36e070b850d31` |

三名复核者均重新核对以上最终指纹。后端最后一次限定核对证明 C2 除新增明确的邀请注册写路径外，与前轮已审字节一致；没有以旧指纹结论覆盖未审的新行为。

## 独立分工与结论

| 只读子代理 | 范围 | 最终结论 |
| --- | --- | --- |
| `/root/ps08_browser_gate_map` | 020/B2 兼容、目录/视图与 SQL、锁序/撤权、迁移/备份、日期与游标 | 原五项 P2、补充 PATCH 兼容和名字派生键实际写路径均已处理；最终未发现新 P0/P1/P2；可以提交书面规格审阅 |
| `/root/ps08_model_tool_boundary_map` | 五视图、计数/分页、日期操作、409/403/404、切账户/项目、未保存设置与键盘/两视窗 | 两项 UI P2 已核销，最后兼容/恢复修订没有引入交互矛盾；最终未发现可操作 P0/P1/P2 |
| `/root/asset_task_046_type_support` | 25 行矩阵/主任务映射、完整范围、后继依赖、路由和指定承接授权 | Windows 主任务映射 P2 已核销；可自定义目录、五视图和附件/子待办/数据源后继保留，尚未扩大实施授权 |

这些结论只表示规格层完整性、一致性及可测试性；实施后仍必须绑定新代码 SHA，逐片独立代码审查、测试和真实旅程验收。

## 核销的问题

| 问题 | 作者最终修订 |
| --- | --- |
| 将已归档项目 404 错称现有统一规则 | 明确当前待办/评论/图片保持成员 ACL；新目录/共享视图配置写入用 409/project_archived；完整资源归档/恢复另属 PS-09 |
| nullable NOT IN 真值不明确 | 固定为完整 IN 补集，显式 IS NULL/IS NOT NULL；A/B/null 双库对照样例列入验收 |
| 已保存处理人条件退组后无恢复路径 | 保留共享定义并给专用 409；member 可明确临时移除条件或选择其他视图，管理者比较后保存修复；合法 override 不被旧条件阻断 |
| 长标题/无上限名字的排序值超过游标容量 | 改紧凑 ID/version/指纹锚点；同快照验证完整条件并重建排序键，与 ORDER BY 共用原语；不截断完整排序、不把长值放进游标 |
| SQLite 旧父表重建可能损害 B2 子表 | 增加 `(project_id,todo_id)` 父键唯一；受控重建、正式 FK 目标、foreign_key_check、评论/图片/用量/字节保留、失败与重放是独立门禁 |
| 旧 expected_version-only PATCH 被含糊写成 422 | 明确保持既有 400/no_change、版本不变；422 只用于新增孤立目录 revision 配对错误 |
| 名字派生键维护可能遗漏注册/恢复直接 SQL | 明确 UserRepo 之外的 InviteRepo.accept 和 snapshot.upsert_users_into_pool；按最终名字同事务重算，不依赖先前迁移、不信任包中派生值 |
| 切视图“保持”语义不明确 | 继续编辑=停留原视图；舍弃并切换=清草稿后载入目标；管理者保存后切换遇失败仍留原草稿；不跨视图沿用 |
| Windows 安装/更新挂在视觉主任务 022 | 改为主任务 021（Windows）、022（视觉），项目 UI/交付仍独立跟踪 |

处理人条件的问题同时由后端和前端指出，不能重复计为两项独立功能进展。原 API 空 PATCH 400/no_change 源码断言、旧 SQLite 的级联外键、真实 PG 顺序/fake 测试证据级别等来自本次只读源码核对，本轮没有执行它们。

## 提交范围与验证边界

本批唯一允许提交的六个路径为本记录、三份规格，以及只新增候选状态的 `PROJECT_SPACE_EXECUTION_PLAN.md`、`PROJECT_SPACE_GAP_AUDIT.md`。没有业务/测试代码。既存未跟踪 `dashboard/src/pages/Agent/Workspace/components/WorkspaceDrawer.privateTask.test.tsx` 必须保留在提交外，原 SHA-256 为 `9b76abd86f29ba8018067f7e46f283e3e5bc32bd6d0f26a40647255a84d026f0`；030 文件模式的 False/NO-GO 和 045-F 待承接状态未被本批改写。

提交门禁核对六路径、三规格原字节与 index blob、所有相对文档链接、无占位标记/空白格式错误，以及索引没有混入既存未跟踪测试。仅为规格文档，不运行镜像业务实现的测试，不把此前 PS-04B 回归或浏览器计数当本批测试。Windows 当前没有 make，本批采用已使用的文档提交流程 `SKIP_PRECOMMIT=1`；这不表示 make precommit/make all 或 Dashboard build 已通过。本批不能作为代码 ship bar 通过的凭据。

用户书面批准后才写实施计划与 TDD 任务包；Codex 或替代审查的例外授权需明确覆盖本批 C1/C2，原 B1/B2、045-B 不扩大。远端只向用户 `xiongbao/main` 非强制推送，不部署。

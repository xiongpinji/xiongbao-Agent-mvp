# 026 · 项目资产版本第一片验收记录

状态：本地实现与定向验收通过；固定提交 GLM 只读审查、远端快进推送仍须完成。本记录只覆盖 PS-06B-1，不核销完整 PS-06B 或 WorkBuddy 1:1。

## 行为边界

项目成员可在同一文件下列出不可变版本、上传新版本、下载任一历史版本，并把已有版本恢复为当前版。恢复不复制文件或创建版本；恢复前校验私有对象是安全的普通文件且大小匹配。服务端用 `(project_id,node_id,version_id)` 约束历史操作，并在写事务内重查成员、归档状态和节点；当前版幂等恢复也经过这些检查。非成员、跨节点版本、撤权后的旧链接均返回统一 404，归档项目禁止版本写入。

项目资产页提供“版本管理”入口及双栏弹窗。左侧显示版本时间/当前版，右侧显示安全元数据、历史下载和恢复操作。上传文件名不修改节点名。当前版字节计入容量显示，旧版本保留独立对象；项目配额尚未建立。

## 独立验证

| 验证 | 结果 | 边界 |
| --- | --- | --- |
| Windows `tests/unit/db/test_project_assets.py` | 82 通过、5 项 POSIX 专项跳过 | 覆盖版本排序、包含关系、成员/归档锁序、失败回滚、缺失/截断/符号链接对象、幂等恢复；fake PG 仅验证 SQL 顺序，不是 PG 实库 |
| WSL 同一单元文件 | 87 通过 | POSIX 目录和 no-follow 安全用例实际执行 |
| Windows 与 WSL `tests/integration/test_project_assets_api.py` | 各 25 通过 | HTTP 状态、响应形状、真实对象字节和授权 |
| 前端项目页 Vitest | 137/137 通过 | 8 个项目页测试文件；另有 API/版本弹窗/父页 3 文件定向 48/48 |
| 静态/构建 | 后端目标 Ruff check/format、3 个源文件 mypy；前端目标 ESLint/Prettier、`tsc -b` 和生产构建通过 | Vite 大包警告仍在，非构建失败 |
| 隔离服务 + 无头 Chrome | 三身份旅程通过 | owner UI 创建项目和版本弹窗；member 接受邀请并上传；验证历史下载字节、UI 上传/恢复、跨节点 404、移除成员后原令牌 404。测试环境用本地假 provider 跳过验证码，不是正式用户登录/安装包验收 |

本地截图：工作区 `outputs/xiongbao-project-asset-versions-026-list.png`、`outputs/xiongbao-project-asset-versions-026-modal.png` 和 `outputs/xiongbao-project-asset-versions-026-modal-narrow.png`。它们是熊宝 1280×768 与 760×720 的候选页面证据；截图没有构成 WorkBuddy 逐像素验收。

本批没有运行全仓 `make all`：当前 Windows shell 未安装 `make`，因此本地 Git hook 的 `make precommit` 也不能在此环境执行。上述命令由 Codex 分别运行并验证退出码；完整非 live 套件及 Linux/Windows 托管 CI 仍是未验证项，不能用定向结果替代。

## WorkBuddy 对照和未达项

2026-09-25 对 WorkBuddy 5.6.2 既有项目只读观察到：资产表格带名称、类型、更新人、更新时间、大小，文件预览和宽版版本管理弹窗可联动，所观察文件只有第一版。熊宝这批保留双栏版本信息结构和当前版标识，但仍以明确占位代替文件内容预览；没有验证 WorkBuddy 的多版本恢复状态。资产删除/回收、项目配额、移动/重命名、添加到任务、真正项目任务输入区与项目配置仍属后续批次。真实 PostgreSQL 迁移/并发、完整 Windows 安装路径和两产品逐状态视觉也尚未通过。

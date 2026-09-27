# PS-01／018 项目基座：PostgreSQL 新库与 v17 升级补验

验证日期：2026-09-27。当前源码固定为 `0cbe14960c06654e946dcc7ecc1c706b8ce56c61`；历史起点使用首次加入 018 迁移的前一提交 `332b6d1c54d9a37804bca5840fb84fbbe34316b8`。在一次性 PostgreSQL 18.6 集群的两个**新建**数据库运行，监听 `127.0.0.1:39151`，与本机 5432 和用户既有库隔离；测试后服务已停止，未执行 `DROP SCHEMA` 或清理用户数据。

源码绑定先于运行检查：历史阶段的 `octop.__file__` 必须位于 v17 隔离工作树的 `src/`；新库及升级阶段必须位于当前提交的隔离工作树的 `src/`。这避免了 Windows editable 虚拟环境曾出现的“测试文件是新检出、实际包却来自旧检出”误证。探针还只接受 `127.0.0.1`、非 5432 端口和 `ps01_fresh`／`ps01_upgrade` 两个一次性库名。

| 实库路径 | 执行与结果 | 已证明的范围 |
| --- | --- | --- |
| 历史起点 | 用 v17 源码执行其全部迁移：版本为 **17**、`project_spaces` 不存在；创建测试用户，退出码 0 | 升级输入确为不含项目表的旧库，且有需保留的数据 |
| 新库 | 用当前源码在空的 `ps01_fresh` 执行全部迁移至 **v30**；项目仓储探针通过，退出码 0 | 项目创建后 owner 成员记录存在；owner/member 项目列表与大小写不敏感搜索、outsider 不可列举；改名刷新后名称持久 |
| 旧库升级 | 在已含 v17 用户的 `ps01_upgrade` 上用当前源码迁移至 **v30**；旧用户 ID 保留，随后同一项目仓储探针通过，退出码 0 | 真实 PostgreSQL 从 017 形状到当前形状的升级链可运行，旧用户不丢，018 项目仓储可用 |

项目仓储探针在每个当前形状的库中新建一个合成项目及 owner、member、outsider：成员加入前仅 owner 可列举，加入后 member 可搜索而 outsider 仍为空；owner 改名后，再从数据库读取并以新名称搜索。这里的“不可列举”是 `ProjectRepo.list_for_user` 的仓储结果，**不是** HTTP 鉴权、深链或浏览器 UI 的证明。新库与升级路径均明确断言版本等于 30；[PostgreSQL 运行记录](evidence/ps01-20260927/ps01_pg_transcript.txt)依次输出 v17、全新 v30、v17→v30 三项 PASS，退出码 0。

另在**独立的合成 SQLite 用户资料副本**运行了已登录浏览器旅程，前后端均从上述当前源码工作树启动。后端启动时显式设置 `PYTHONPATH` 指向该工作树的 `src/`；[运行进程源码绑定记录](evidence/ps01-20260927/ps01_browser_source_binding.jsonl)及其[采集脚本](evidence/ps01-20260927/capture_source_binding.py)核验了后端与 Vite 进程的工作目录和运行环境，并在同一 Python 与 `PYTHONPATH` 下确认包导入路径。借用虚拟环境的首次预检曾导入其他工作树的 editable 包，已从固定 SHA 验收证据中排除。最终使用 Windows Chrome／Playwright，在 1280×768 下分别登录合成 owner、member、outsider，退出码 0：

- owner 在 UI 创建项目，member 通过邀请接口加入；member 可在项目页搜索、进入详情，且无编辑按钮。
- outsider 搜索为空；同一项目详情 HTTP 返回 404，直接打开深链后页面没有显示项目名。
- owner 在 UI 重命名，刷新详情与重新搜索仍显示新名称；member 刷新详情也看到新名称。三账号浏览器运行没有页面脚本异常。

最终浏览器运行的项目 ID 为 `01M3HDTYHP5ZQWKXRVWFQG8XV9`，名称从 `PS01验收原名4` 改为 `PS01验收新名4`。[浏览器运行记录](evidence/ps01-20260927/ps01_browser_transcript.txt)与五张 1280×768 截图保存在 `evidence/ps01-20260927/`：owner 创建、member 搜索、outsider 深链、owner 改名搜索、member 刷新。证据文件不含测试用户令牌。浏览器旅程使用合成 SQLite 副本，**不等于** PostgreSQL 上的 HTTP/UI 验收，也不等于真实账号、生产或 WorkBuddy 1:1 视觉验收。

本次 PostgreSQL 只覆盖最小旧数据与新库路径；未迁移真实历史任务/邀请/文件大库，没有证明全部迁移的每个故障窗口、并发创建/改名或生产升级耗时。PS-01 的 WorkBuddy 1280×768 逐状态视觉核对仍未完成；不能据此核销整条 PS-01 或 25 项矩阵。

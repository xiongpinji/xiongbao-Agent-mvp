# PS-03 项目动态 P1/P2 收口：父页撤权与加载更多网络提示

2026-09-30，前端两片依次提交并非强制推送至 `xiongbao/main`：P1 `a95cf4e9d4bffb2e6df831fb17a66ae2fb796922`，P2 `2078e56b9307942108815696b52279899a6cd035`。当前记录仅核验这两处此前在[固定 PG/TCP 局部验收](PROJECT_ACTIVITY_PS03_TCP_PG_ACCEPTANCE_20260930.md)中发现的界面缺口，不核销完整 PS-03、WorkBuddy 1:1 或 25 项总目标。

| 缺口 | 当前行为与提交 | 验收证据 |
| --- | --- | --- |
| P1：动态收到当前项目 404 后父项目详情仍保留旧指令和成员卡 | `ProjectActivity` 将当前请求的 404 通知 `ProjectDetail`；父页立即清空项目和成员缓存，作废在途父级读取并拒绝旧编辑保存回填。旧项目、旧账号、旧筛选的迟到响应不能撤下新授权页。`a95cf4e9`。 | 先写的 4 项目标测试在旧行为下失败，修复后项目两组 70/70；独立只读复核 0 个 P0/P1/P2。隔离 SQLite、合成 owner/member/outsider 与真实无头 Chrome 中，成员看到项目名、描述、指令及 2 人计数后被移除；动态刷新转固定无权页，旧私密 DOM 和操作入口消失。成员后续活动 GET/留言 POST 为 404，owner 仍可读、outsider 仍被拒。截图和脚本保存在仓库外同级 `work/qa-project-activity-20260929/ps03_p1_browser_v1/`。 |
| P2：有已载入动态时，追加网络失败裸显 `Failed to fetch` | 仅“加载更多”的无结构化 API 响应网络失败显示现有 `projects.activity.loadFailed` 文案；服务端错误详情、404 清权和无效游标分支仍按原语义处理。保留旧行和游标，重试同游标。`2078e56b`。 | 新增网络测试先因缺少“加载动态失败”而真实 RED，修复后项目两组 72/72；结构化 API 错误保留详情的回归通过；独立只读复核 0 个 P0/P1/P2。隔离 SQLite/真实 Chrome 受控中断一次追加 GET，20 行保持，显示中文失败提示，重试同游标后 26 行无重复、提示消失。截图和脚本在同级 `work/qa-project-activity-20260929/ps03_p2_browser_v1/`。 |

P1 的完整前端复跑为 249 文件、1913/1913 通过（`--maxWorkers=2`）；第一次默认并发执行为 1910 通过、3 个未改文件中的 5 秒超时，相关两组单独复跑 15/15，通过与失败均保留。P2 的完整前端复跑为 249 文件、1915/1915 通过（`--maxWorkers=4 --testTimeout=10000`）。两片各自的 Windows 单次提交钩子按独立审查差异哈希、精确暂存白名单和浏览器结果放行，并重新通过聚焦 Vitest、ESLint、Prettier、`tsc -b` 与生产构建；可复查脚本及不含账号夹具的输出位于仓库外同级 `work/qa-project-activity-20260929/ps03_p1_browser_v1/`、`ps03_p2_browser_v1/` 的 `hooks/` 与 `commit_gate_*.log`。提交使用一次性 `core.hooksPath`，未改变永久 Git 配置。P1 的 Ruff check/format 与 mypy（558 个源文件）由本次命令输出记录，未纳入独立文档复核范围。本机无 `make`，因此未运行原样 `make all`；本次仅改前端，也未重跑后端全量 pytest。

此前固定源码 `d8bd2f2` 的真实 PostgreSQL/TCP 证据证明服务端撤权后新读取被拒，但**新前端两片没有重新完成同一 PG/TCP 加浏览器组合旅程**。本次浏览器使用隔离 SQLite 与合成账号，不是用户实际安装、生产账号、付费模型或 WorkBuddy 桌面逐状态像素/键盘验收。GLM-5.3 因当前额度未对新提交补审；独立只读子代理审查和本地测试是本次明确记录的证据，不冒充 GLM 结论。

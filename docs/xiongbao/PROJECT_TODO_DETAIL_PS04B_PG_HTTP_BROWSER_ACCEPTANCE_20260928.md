# PS-04B PostgreSQL HTTP 与 TCP 浏览器补验（2026-09-28）

状态：指定两份后端测试的 **真实 PostgreSQL 38/38、SQLite 兼容 38/38**，以及 **13 阶段真实 TCP/真实 PG 浏览器旅程**全部通过。HTTP/测试适配与最终浏览器均经独立只读复核，未发现可操作 P0/P1/P2。本记录补充 [B1](PROJECT_TODO_DETAIL_PS04B_B1_EVIDENCE.md) 与 [B2](PROJECT_TODO_DETAIL_PS04B_B2_EVIDENCE.md)，不核销 WorkBuddy 项目空间整体 1:1。

## 源码与唯一适配

执行基线为 `e72f72758a4f91c00738127e1a46850c9fd54174`。原评论图片撤权串行化测试把第二连接写死为 `SqlitePool(srv.services.db.path)`；在真实 PG 上先得到目标 RED：setup/teardown 通过，call 仅因 `PostgresPool` 没有 `.path` 失败。改为现有正式 `open_database(srv.config, srv.services.paths)`，并更新对应导入。锁等待、读完再撤权、撤权后拒绝和 finally 关闭第二池的断言完整保留。

当前测试文件 SHA-256 为 `a186d40c` 开头、`7a9bf44` 结尾；RED 的旧文件为 `280c9aca` 开头、`7ae6a11` 结尾。独立审查在内存中逆转这两行后精确恢复旧指纹。业务 Python、迁移和前端字节均未改动，030 文件模式开关仍为 `False`。仓库中原有未跟踪的 030 私密任务 UI RED 用例未纳入本次提交，指纹仍为 `9b76abd86f29ba8018067f7e46f283e3e5bc32bd6d0f26a40647255a84d026f0`。

## 后端结果

测试入口始终为 `uv run --no-sync pytest`，指定：

- `tests/integration/test_project_todos_api.py`
- `tests/integration/test_project_todo_comments_api.py`

| 证据 | 结果及边界 |
| --- | --- |
| RED-PG | 唯一目标测试精确失败于 SQLite 专用辅助假设；不是产品行为断言失败。原始 [JUnit](evidence/ps04b-pg-http-browser-20260928/http-suite/red-pg/junit.xml) 和阶段记录保留。 |
| GREEN-PG | **38 passed，149.16 秒**；38 个唯一用例、114 个 setup/call/teardown 报告均通过，没有跳过或 fixture_error。**34 项经过 FastAPI/httpx ASGITransport，4 项直接 service**；3 个故障注入标签是这 38 项的子集，不另加计数。直接 service 的故障用例不能写成真实 HTTP 500。 |
| PG 生命周期 | 38 个 server 正常 start/stop、39 个 PG pool 各关闭一次且底层已关闭；每用例剩余 backend connection 为 0，logger/patch/环境恢复全部通过。多出的池是串行化测试的第二连接。 |
| SQLite 兼容 | 第二版 runner **38 passed，111.94 秒**；JUnit 错误/失败/跳过均为 0。独立审查读取全部 38 份列出的 DB 文件，实际头均为 `SQLite format 3`。私有 Job 收尾 active=0、handle 已关闭。 |
| 隔离绑定 | Windows 原生 Python 3.13.13；一次性 WSL PostgreSQL 18.6，端口 43317。连接前及迁移前核对专用 DB 前缀、随机用户、端口、server address、data_directory；不以环境变量或 URL 推断实际驱动，不连接生产库。 |

原始 [PG 绑定](evidence/ps04b-pg-http-browser-20260928/http-suite/green-pg/pg-http-bindings.json)、[PG 命令结果](evidence/ps04b-pg-http-browser-20260928/http-suite/green-pg/command-result.json) 和 [SQLite 命令结果](evidence/ps04b-pg-http-browser-20260928/http-suite/sqlite-compat-v2/command-result.json) 均在仓库中。

## 实际 TCP 浏览器旅程

第二套专用 PostgreSQL 集群使用端口 47687；真实 Uvicorn API 和 Vite 前端均绑定独立 loopback 端口，浏览器通过同源 Vite API 代理读取正式路由。每次使用新 DB、新 home、新合成 owner/member/outsider；Chrome 153.0.8010.53 的私有 `playwright_chromiumdev_profile-*` 目录与创建它的 Node 父 PID 已核对。Chrome 子进程仅恢复正常 Windows `USERPROFILE`，API/Vite 的 HOME、USERPROFILE、OCTOP_HOME 仍完全隔离；没有使用默认 Chrome profile。

服务器使用测试 `FakeHarnessManager` 并关闭主动任务，未调用付费模型。待办 repo、成员权限、评论、图片存储、上传和 HTTP 没有 mock；浏览器脚本没有拦截或伪造网络响应。owner 经实际登录页与本地 slider 手势登录；member/outsider 经真实本地登录 API 后把各自 token 放入各自隔离 context。

| 阶段 | 实际通过的行为 |
| --- | --- |
| 1–3 | 依赖/源码/私有 Chrome 绑定、owner 真实页面登录、合成项目及两种格式待办、邀请入组。项目数据经真实 API 创建，不宣称测试了创建项目弹窗。 |
| 4 | 表格与看板打开同一 todo ID；plain 正文逐字保留、不解释 Markdown；深链、刷新、浏览器前进/后退、Escape 与触发按钮焦点恢复。 |
| 5–6 | 安全 Markdown 视图/预览，原始 HTML/危险链接不执行、载荷外部图片未请求；正文保存后刷新逐字和版本核对；真实第二 PATCH 造成 409，原草稿保留，手动刷新比较后再保存成功。 |
| 7–8 | member 文字评论，owner 刷新可见；合成粘贴 PNG/JPEG/WebP 顺序图片和文字，实际 multipart POST 201。两身份授权 GET 的 media type、`private, no-store`、`nosniff` 及三图原始字节均匹配；评论 DTO 不暴露存储键/路径。 |
| 9 | owner/member/outsider 各在 1280×768 和 800×728 使用隔离 context；授权身份可见评论和图片，外人待办/评论/三图共 5 个旧 scoped URL 均返回 404，深链页面不显示私密内容。 |
| 10 | 经项目列表进行实际 SPA 项目切换；新详情不携带旧项目私密评论、图片或未发送草稿。没有模拟直接 prop 替换的并发晚到响应。 |
| 11–12 | 移除成员后旧 token 的同组 5 个 URL 均 404，刷新两尺寸页面不残留私密标题/正文/评论/图片；软删待办后 owner 同组旧 URL 均 404。 |
| 13 与外层门禁 | 13 阶段全部 PASS、页面错误 0、载荷外部图片请求 0；6 个 context 和 browser 正常关闭。Node rc=0、API 正常 stop、实际池关闭/DB 连接为 0、3 个私有 Windows Job active=0/handle 关闭、API/Vite 端口关闭；完整执行与收尾期间源码/HEAD/index/未跟踪集合均不变。 |

[实际旅程](evidence/ps04b-pg-http-browser-20260928/tcp-browser/browser-pg-attempt-3/journey/result.json)、[外层收尾](evidence/ps04b-pg-http-browser-20260928/tcp-browser/browser-pg-attempt-3/command-result.json) 和 [PG 服务器绑定](evidence/ps04b-pg-http-browser-20260928/tcp-browser/browser-pg-attempt-3/server-result.json) 必须配套核对。日志另保留 member/1280 的三条私有图片 GET `net::ERR_ABORTED`，不能写成所有网络请求零失败；已完成的授权读字节比对与各撤权/删除 404 断言仍通过。三张图片是 3×2 像素合成夹具，截图中的红色小点不证明真实大图布局。截图例：[owner 宽屏](evidence/ps04b-pg-http-browser-20260928/tcp-browser/browser-pg-attempt-3/journey/screenshots/owner-authorized-detail-1280x768.png)、[member 800 宽](evidence/ps04b-pg-http-browser-20260928/tcp-browser/browser-pg-attempt-3/journey/screenshots/member-authorized-detail-800x728.png)。

## 失败历史、进程关闭与归档

首轮 SQLite runner 因不存在 `subprocess.CREATE_SUSPENDED` 在创建 child 前中止；第二版使用 Windows 数值 0x4，并保存独立目录。首轮 Job smoke 因 venv redirector 实际生成 4 进程而不满足预期 2，保持 INCONCLUSIVE；v2 使用基础解释器观察到实际父/子共 2 进程，超时后 active=0、handle 关闭，独立检查对应 PID 均不存在。

第一套集群的浏览器准备先因错误假定公共 captcha 为 none 中止，随后 Chrome 在伪造 USERPROFILE 的环境中启动失败。独立正常 Windows 环境的私有 Chrome launch/close 探针通过。第二套集群保留两次脚本问题：AntD 两字按钮插入空白导致登录定位失败；17 字符合成项目名超过正式上限 15 导致 422。只修执行夹具，最终 attempt 3 通过；各失败结果和当次脚本快照均未覆盖。

两套 [旧集群](evidence/ps04b-pg-http-browser-20260928/http-suite/cluster-result.json) / [浏览器集群](evidence/ps04b-pg-http-browser-20260928/tcp-browser/cluster-result.json) 均正常 STOPPED：stop_code=0、status_after=3、各自端口已关闭。未递归删除临时目录。归档 [manifest](evidence/ps04b-pg-http-browser-20260928/manifest.json) 记录 80 个原始文件来源、长度和 SHA-256，复制字节逐项一致；Git 对这些原始证据禁用换行转换。没有归档 raw stdout/stderr、用户配置、DB 或浏览器 profile，JSON/XML 扫描未见原始 JWT。

## 审查与剩余边界

HTTP/测试适配独立只读复核未发现可操作 P0/P1/P2，核对了 RED 指纹逆转、GREEN 全阶段/身份/池、实际 SQLite 文件头和关闭状态。另一独立只读复核核对浏览器 13 阶段实际断言、1973 项源码/脚本绑定、四份脚本快照、12 张截图及 Windows/WSL PID 和端口，未发现可操作 P0/P1/P2。见[审查摘录及指纹](evidence/ps04b-pg-http-browser-20260928/INDEPENDENT_REVIEW.md)；审查主体为独立 Codex 子代理，**不冒称 GLM 补审**。

本轮 `ruff check src tests`、`ruff format --check src tests`（1129 文件）及严格 mypy（546 源文件）通过。Windows 未安装 make；没有执行 `make all`，没有在本轮重跑全仓 pytest 或前端构建。B2 的历史非 live 4593 passed、160 skipped、22 warnings 属于先前固定代码验收，不能作为本轮新执行结果。本次提交使用已记录的 `SKIP_PRECOMMIT=1` 绕过依赖 make 的钩子，不宣称钩子通过。

尚未验证：真实失败上传保留草稿及手动重试、OS 剪贴板、Blob 内存回收、跨项目晚到响应并发矩阵、全套 PG 并发/配额/故障矩阵、实际历史库升级、完整键盘/滚动几何、WorkBuddy 逐状态视觉、日期/优先级/标签/可配置视图、付费 provider、GLM、TLS 与部署/用户安装验收。另有[真实 PG tar 数据库及评论图片切片补验](PROJECT_TODO_DETAIL_PS04B_PG_BACKUP_ACCEPTANCE_20260928.md)，不扩大为其他系统目录或聊天保留的完整备份验收。本片不修改 030/PS-08 或 045-F 的路由承接与授权状态。

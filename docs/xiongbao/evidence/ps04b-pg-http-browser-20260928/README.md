# PS-04B PostgreSQL HTTP 与 TCP 浏览器补验证据

本目录保存 2026-09-28 一次性合成环境的原始 JSON、JUnit、执行脚本快照和截图。源码基线为 `e72f72758a4f91c00738127e1a46850c9fd54174`，唯一测试适配是 `test_project_todo_comments_api.py` 的两行驱动选择修正。业务源码和 Dashboard 未修改。

- `http-suite/red-pg`：先复现 PostgreSQL 不存在 `.path` 的测试辅助假设；失败被精确记录。
- `http-suite/green-pg`：指定两个文件 38 项通过，其中 34 项 ASGI、4 项直接 service；阶段/服务/池身份在 `pg-http-bindings.json`。
- `http-suite/sqlite-compat`：首轮 runner 在创建 child 前中止。实际通过记录单列于 `sqlite-compat-v2`，38 项通过并核对实际 SQLite 文件头。
- `http-suite/job-timeout-smoke*.json`：第一轮计数假设不符，v2 实际两进程超时关闭验证通过。
- `http-suite/browser-pg*`：第一套集群上的两次浏览器准备失败均保留；不得视为旅程通过。
- `tcp-browser/browser-pg`、`browser-pg-attempt-2`：第二套集群上的按钮定位、项目名数据错误均保留。
- `tcp-browser/browser-pg-attempt-3`：13 阶段真实 TCP/真实 PG 浏览器旅程 PASS；`command-result.json` 的外层收尾门禁和 `server-result.json` 的 DB 绑定必须一起读。
- 两套 `cluster-result.json` 均为正常 STOPPED。原始临时目录未递归删除。
- `manifest.json` 逐文件记录来源、长度和 SHA-256，归档 80 个原始文件。原始证据设置 `-text`，避免 Git 自动转换换行损坏字节绑定。

脚本快照用于复核当次执行；它们包含原始绝对路径、一次性输出约束及本地依赖路径，不能在本归档目录直接重跑。重跑需另建一次性 QA 输出，并重新绑定集群身份、源码及私有进程树。脚本中的账户和密码均为合成夹具。

未复制 stdout/stderr、合成 home、DB 文件、私有图片存储、浏览器 profile 或真实用户配置。JSON/XML 未包含原始 JWT。归档复制后逐文件哈希与来源一致；本 README 和后续人工审查记录不属于 80 个原始文件。

原始文本含 Windows CRLF，本目录 attributes 仅对归档 JSON/XML/Python/CJS 启用 `cr-at-eol` 识别，同时保持 `-text`，避免 Git 将原始回车误报为行尾空白或自动转换换行。独立包装审查还发现两份原始脚本 `http-suite/tcp/verify_sqlite_v2.py` 与 `archive/archive_ps04b_evidence.py` 保留了末尾额外空行；只对这两个确切路径关闭 `blank-at-eof` 检查，其它 whitespace 检查保留。没有改写原始脚本或 manifest 指纹，也没有扩大到业务源码。

最终暂存检查另发现 `http-suite/red-pg/junit.xml` 的原始失败 traceback 第 13 行带行尾空白；只对该确切 XML 路径关闭 `blank-at-eol` 检查，保留其它检查及原始失败文本。新写的审查说明已正常整理末尾空行，不使用原始证据例外。

范围与未验项见 [补验记录](../../PROJECT_TODO_DETAIL_PS04B_PG_HTTP_BROWSER_ACCEPTANCE_20260928.md)。

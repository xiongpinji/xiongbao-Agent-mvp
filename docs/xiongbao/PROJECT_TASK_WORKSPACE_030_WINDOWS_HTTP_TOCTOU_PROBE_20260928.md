# 030A Windows HTTP 文本文件检查与使用竞态

日期：2026-09-28。执行源码固定为 `cb72fe4833711f8a32bdbb8bcd2df58a4299c856`。**真实文件任务运行体的 ASGI HTTP 入口：稳定植入 junction 的 GET／PUT 均返回 403；检查通过、打开前替换受管子目录后，GET 返回根外合成 canary，PUT 覆盖根外已有合成文件。** 结果为 `COMPLETE_WITH_HTTP_CHECK_USE_ESCAPE`。退出码 0 只表示诊断与复原完成，不是安全测试通过。产品磁盘开关仍为 `False`，激活仍为 NO-GO。

本轮执行 [B+ 设计 §7.2](PROJECT_TASK_WORKSPACE_030_TOCTOU_DESIGN.md) 已允许的确定性接缝诊断，补充[先前工具组件诊断](PROJECT_TASK_WORKSPACE_030_WINDOWS_TOCTOU_PROBE_20260928.md)缺少的 HTTP 路径证据。没有修改产品代码、依赖或开关默认值，也没有实现竞态防御。

## 四组真实结果

| 病例 | 目标路由 guard 通过／拒绝 | 成功返回的后端检查 | 精确目标打开命中 | HTTP | 根外合成结果 |
| --- | --- | --- | --- | --- | --- |
| stable-read | 0／1 | 0 | 0 | 403／internal | 未返回 canary，外侧字节未变。 |
| race-read | 1／0 | `materialize_local` 1 次 | `Path.open` 1 次 | 200 | 响应内容精确等于根外 canary；外侧字节未变。 |
| stable-write | 0／1 | 0 | 0 | 403／internal | 外侧已有文件未被覆盖。 |
| race-write | 1／0 | `_resolve_path` 1 次 | `os.open` 1 次 | 200 | 外侧已有 `target.txt` 被覆盖为精确合成写标记。 |

每组先通过同一运行体、干净目录的 HTTP 正例：GET 返回根内标记；PUT 在根内创建并核对 `baseline.txt`。四组正例均为 200，外侧 canary 在基线后仍保持原字节。

随后每组分别验证 project owner、admin、outsider，以及任务本人带 `as_user` 的请求，共 **16 次 403／internal**；每次都在目标路由 guard、后端目标检查和精确打开之前拒绝，三个计数均为 0。实际文件运行体所有者是项目 member。这里未移除项目成员，不把这组身份矩阵当作成员撤销后的私密文件语义验收。

## 打开接缝与实际调用序

GET 走 `project_task_file_io_path → BackendWorkspace.aread_text/read_text → materialize_local → Path.read_text/Path.open`。它使用安装版 workspace 的本地快路，未进入 `FilesystemBackend.read`。观察器先委托原 guard 和原 `materialize_local`；二者成功返回后才记录通过。仅精确匹配目标 Path 的 `open(mode="r")` 包装器注入交换，随后以原参数调用原 `Path.open`。记录的安装版栈位于 `workspace.py:643`。

PUT 走路由检查、`_backend_storage_key`、`aupload_bytes/aupload_files`、`FilesystemBackend.upload_files`；原 `_resolve_path` 成功返回、父目录 mkdir 完成后才命中精确目标 `os.open`。本次真实 flags 为 769，安装版调用位于 `filesystem.py:1430`。包装器用原路径、flags 和参数委托原打开，不替换上传实现、文件字节或响应。

竞争两组记录顺序为 `route_guard_started → route_guard_passed → materialize_returned/backend_resolve_returned → target_open_wrapper_entered → junction_installed`。包装器进入事件本身不代表 OS 打开成功；实际 200 响应和根外字节确认了后续效果。每组只交换一次、只打开目标一次，其他打开原样转发。稳定对照是在请求前交换，原 guard 拒绝，目标打开为 0。

## 数据隔离、模型与复原

原生 Windows 11、C: NTFS、Python 3.13.13、`deepagents 0.7.9`、发行包 `orcakit-harness-agent 1.0.14`。四组在全新合成 Temp 的不同 home 中通过公共 ASGI 项目任务 POST 创建真实 AgentManager／Harness 运行体，创建响应均为 201；数据库为 SQLite，不是 PostgreSQL 或 TCP 服务。

每组启动前隔离 HOME／USERPROFILE／OCTOP_HOME／PATH，暂时移除数据库覆盖环境和验证码配置；配置验证为该新 home 内的 SQLite，真实 `SqlitePool` 构造前再验证路径，连接后核对 `PRAGMA database_list` 的 main 身份。原环境变量值只在诊断进程内快照，用于恢复和结束一致性核对，没有输出或写入结果，也未用作测试数据库／验证码配置。proactive 定时循环仅在诊断进程停用；模型工厂在启动前固定为本地录制模型，四组实际模型调用均为 0。结果中的外部模型请求 0 依据该固定工厂，不是网络抓包或整台机器流量证明。

唯一参与交换的 `child`、`child_saved` 与 outside 均属于 `C:\Users\canqu\AppData\Local\Temp\xiongbao-030-http-toctou-vchaskzr`。请求结束后，只摘除仍明确指向本组 outside 的 junction，再复原保存的原目录；四份根内原 `target.txt` 均保持 INSIDE，外侧文件名集合均未变。race-write 的外侧覆盖标记留存为证据，其余三份外侧 canary 保持原字节。

四组真实 `stop()` 均调用一次、无错误，返回后 services／app_runtime 为空且 `_started=False`。随后恢复工厂、proactive、gate、环境、文件打开与 SQLite 构造补丁；关闭本组新增日志 handler，恢复 root／uvicorn／httpx／httpcore 的 handler 和 level。最终非跟随遍历 **878 项**，剩余重解析点为 0。Temp 保留，无递归删除。

独立父进程 watchdog 设整体 300 秒硬超时；实际未超时，子 PID 45700 已观察到退出码 0，父执行入口也退出 0，根代理另行确认该 PID 不存在。启动、终止、二次等待或 poll 异常均保留 inconclusive 结果，只有观察到退出码才声明停止；本轮没有实际注入 watchdog 超时或终止失败。

## 原始产物与诊断失败保留

- [四组原始结果](evidence/ps08-http-toctou-20260928/http-race-result.json)、[源码绑定](evidence/ps08-http-toctou-20260928/source-bindings.json)、[初始 Temp 身份](evidence/ps08-http-toctou-20260928/run-context.json)、[watchdog 结果](evidence/ps08-http-toctou-20260928/watchdog-result.json)。执行后已核对 16 个脚本／产品源码／helper／安装版依赖文件的字节数和 SHA-256。
- [执行探针](evidence/ps08-http-toctou-20260928/probe_windows_http_race.py)：28583 字节，SHA-256 `382c23dd2044e33b27b514d1852311863f5bbf0565ba1410024794cc6f0cbf26`。
- [唯一执行入口 watchdog](evidence/ps08-http-toctou-20260928/run_with_watchdog.py)：3511 字节，SHA-256 `d4ee20cde9b2078a3a83f997cb291c1307ee6912158c3752d4e73a6bab8b6a62`。
- 第一轮[失败脚本](evidence/ps08-http-toctou-20260928/first-attempt/probe_windows_http_race.py)、[watchdog 结果](evidence/ps08-http-toctou-20260928/first-attempt/watchdog-result.json)和[原始错误](evidence/ps08-http-toctou-20260928/first-attempt/stderr.txt)保留：错误地读取 `metadata.version("harness-agent")`，在服务启动／目标请求前报 `PackageNotFoundError` 并退出 1。随后用本地 metadata 核对实际包名，仅修正版本读取并新增发行包名字段，在新的 `-v2` 输出目录执行，没有覆盖第一轮。第一轮不能算完成；没有为它宣称 Temp 的完整复原。错误日志以 `.txt` 归档，字节与原 `stderr.log` 相同。

九份归档均与 checkout 外执行产物逐字节一致；局部 `.gitattributes` 关闭本目录 JSON／Python／错误日志的换行转换，保留 JSON 和错误日志的常规空白检查及原 CRLF。成功组的 stdout 含合成首次登录口令，未纳入仓库归档。复现前须核对实际源码和依赖指纹，使用全新输出目录、合成 Temp 和 watchdog；只改 expected SHA 字符串不能证明同一实现。

两名独立只读代理分别复核执行前 I/O 语义与生命周期，并在修正日志恢复、watchdog 异常记录后复核执行结果，未发现剩余 P0/P1/P2。结果复核包括当前 16 个文件指纹、父子退出状态、实际保留的根内／根外字节与独立 878 项非跟随遍历；没有重跑诊断、启动服务或清理目录。这不是原定 GLM 的固定 SHA 补审，也不是激活批准。

## 范围与门禁

结论限于合法任务所有者请求配合同一 OS 账号、精确时点的测试侧子目录交换。它没有证明远程 HTTP 用户独自拥有植入 junction 的文件系统权限，也没有测自然并发的成功率。两个稳定对照支持本次文本读写路径的前置拒绝；两组竞争明确保留 B+ 已排除的 P4 同账号窗口，不能宣称检查时刻包含证明等于使用时刻安全。

未验证其他 HTTP 文档／预览／下载／列举入口、其余模型工具、根和完整祖先链竞争、特殊 reparse 类型、POSIX、完整 PostgreSQL、TCP／浏览器、付费 provider 或整个 WorkBuddy 功能及视觉矩阵。原有 Windows live-rename 三项跳过没有被这次子目录交换替代。030 UI 的 RED 仍未修，GLM 固定 SHA 补审仍待额度恢复；自定义 backend 或上游依赖改动仍须另行设计评审。

本次提交只含诊断归档和台账，Ruff／格式、原始字节与差异检查另行记录。Windows 缺少 `make`，提交显式使用 `SKIP_PRECOMMIT=1`；没有宣称默认 hook／`make all`／新全仓测试或前端构建通过。未纳入未跟踪的 UI RED 测试；磁盘 `PROJECT_TASK_FILES_MODE_ENABLED=False`，PS-08 不核销。

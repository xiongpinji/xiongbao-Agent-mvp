# 030A Windows 文件工具检查与使用竞态：确定性诊断

日期：2026-09-28。执行源码固定为 `60341df2fb02daee97a1da34cb5332f976d5fcb3`，Windows 11、C: NTFS、Python 3.13.13、安装版 `deepagents 0.7.9`。**稳定植入的 junction 被拒绝；检查通过后、目标文件实际打开前替换受管子目录，`read_file` 读到根外合成 canary，`write_file` 在根外写入合成标记。产品默认开关保持 `False`，文件模式激活仍为 NO-GO。**

本轮是 [B+ 安全设计 §7.2](PROJECT_TASK_WORKSPACE_030_TOCTOU_DESIGN.md) 允许的检查与使用接缝诊断，没有实施竞态防御，也没有改变产品代码、依赖或开关。命令退出码 0 表示诊断和复原完成；结果 `COMPLETE_WITH_CHECK_USE_ESCAPE` 明确记录越界，不能归类为安全测试通过。

## 四组结果

| 场景 | 原路径 guard | 精确目标 `os.open` 包装器命中 | 真实工具结果 | 根外合成结果 |
| --- | --- | --- | --- | --- |
| stable-read：第二轮调用前植入 junction | 拒绝 1 次，通过 0 次 | 0 | error | 未返回 canary |
| race-read：检查后、打开前替换子目录 | 通过 1 次，拒绝 0 次 | 1 | success | ToolMessage 包含根外 canary |
| stable-write：第二轮调用前植入 junction | 拒绝 1 次，通过 0 次 | 0 | error | 未创建根外 `new.txt` |
| race-write：检查后、打开前替换子目录 | 通过 1 次，拒绝 0 次 | 1 | success | 根外 `new.txt` 字节与合成写标记一致 |

每组先完成同线程、干净目录的第一轮正例：读得到根内标记，或写入根内基线文件。随后第二轮执行该组诊断，因此不存在“正常工具本来就不可用”的替代解释。四组基线均成功，各 4 次本地模型调用，共 16 次。每次录制的模型可见工具名均恰为 `ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 六个不同名称；本轮只实际调用其中的读、写工具。

## 接缝与真实调用链

探针复用现有真实 Harness 测试 helper 的录制模型、`MemorySaver` 和工具调用线程，运行真实 Harness 图、文件工具 wrapper 及安装版 `FilesystemBackend`。它直接构造受控组件夹具，未走 AgentManager 文件任务创建、HTTP 或 Gateway。普通产品的文件创建门禁始终关闭。

路径观察器先调用原 `_path_veto`，原样保留返回值并重抛拒绝异常，只有成功返回才记录通过。`os.open` 包装器只匹配本组唯一的规范化词法目标，其余打开原样转发。两组竞争记录的顺序均为 `guard_started → guard_passed → target_open → junction_installed`；`target_open` 是进入包装器、委托原打开之前的事件，不能单独算作原 OS 打开成功。

安装版 read 已完成 `_resolve_path` 和 `exists/is_file`，write 已完成路径解析及父目录 `mkdir`，然后才到达这个包装器。包装器确认 guard 通过后只替换一次：把普通 `child` 改名为 `child_saved`，在原词法位置建立真实 NTFS junction 指向同一合成夹具内的 `outside`，再以原路径、flags 和参数调用原 `os.open`。调用栈分别落在安装版 `filesystem.py:449` 的 read 和 `:511` 的 write，flags 分别为 0 与 769。实际成功的工具结果及根外字节证明了后续读写效果，而不是仅凭钩子命中推断。

模型工厂固定返回本地录制模型，没有调用外部 provider。结果 JSON 中的 `external_model_requests=0` 是夹具声明，与这个固定工厂一致；它不是独立网络抓包计数，不能用来推导整个机器零网络流量。启动日志有未配置技能目录 `/_builtin_skills`、`/skills` 的提示，四组基线和诊断均已完成，没有运行时关闭或复原错误。

## 结论的范围

这两次复现确认 B+ 已明示保留的 Windows 检查与使用窗口：同一 OS 账号能够替换受管子目录，并在精确打开时点进行替换，工具的检查时刻包含证明仍可能随后访问根外。它不证明远程用户有这种文件系统能力，也不测自然并发的成功率。

稳定植入的两个对照支持本次组件读写路径上的前置拒绝。竞争结果不能核销或否定其他独立门禁：其余四个工具、HTTP 的 materialize/read/write 路径、根本体与整条祖先链的竞争、Linux/POSIX、特殊 reparse 类型、真实供应商模型、浏览器、完整 PostgreSQL 或 WorkBuddy 功能及视觉对齐都没有在本轮验证。原有 Windows live-rename 三项跳过也没有被这两组子目录交换替代。

后续风险处置应按既有设计单独评审；本轮没有批准自定义 backend、上游依赖变更或翻开关。真实浏览器工作区的独立 RED 仍见 [TCP 浏览器记录](PROJECT_TASK_WORKSPACE_030_PS08_TCP_BROWSER_RED_20260928.md)，PS-08 不核销。

## 证据与复原

- [四组原始结果](evidence/ps08-toctou-20260928/tool-race-result.json)：源码 SHA、模块路径、版本、调用序、工具名集合、合成根外结果及运行体关闭状态。
- [源码与脚本绑定](evidence/ps08-toctou-20260928/source-bindings.json)：执行脚本、两份产品源文件、既有 helper 和安装版 backend 的字节数与 SHA-256；这五项已重新核对。
- [复原记录](evidence/ps08-toctou-20260928/cleanup.json)：四个运行体关闭成功，所有 junction 解除、子目录复原；非跟随遍历 39 个条目后，剩余重解析点为 0。
- [归档探针](evidence/ps08-toctou-20260928/probe_windows_tool_race.py)：供复核精确注入与生命周期；它不是产品功能，也不是默认安全门禁测试。

新建并保留的夹具目录为 `C:\Users\canqu\AppData\Local\Temp\xiongbao-030-toctou-spp4udxr`。所有参与交换的目录和根外 canary 都位于这一个新 Temp 根内，仅解除已确认目标的 junction 并复原目录，没有递归删除。根内原文件和四份根外 canary 均保持原字节；只有 race-write 的根外合成 `new.txt` 留存，作为确认写入副作用的证据。该 Temp 根未接触既有用户数据。

原执行脚本为 12550 字节、SHA-256 `183ae1c80b994d42023cb4b09462adb0053a968be124e632928fd92eee373620`；归档只去掉多余的文件尾空行，保留一个 LF，为 12549 字节、SHA-256 `9a60e1760d60fb89980a82417764b1e73bf9bc9d17c5c163d8e8da0a5ab590b7`。二者 AST 已比较一致，不能称为字节完全相同。原始结果、绑定和复原 JSON 与工作区外的执行产物字节完全相同。本证据目录的局部 `.gitattributes` 只对这四份归档关闭 Git 换行转换，以保留 JSON 原始字节和脚本绑定哈希；JSON 的空白检查保留常规规则，并把原始 CRLF 识别为合法换行。

独立只读子代理已审查执行脚本、原始结果、安装版打开位置及保留的 Temp 内容，没有发现使诊断失效或结论过宽的 P0/P1/P2。它没有修改、重跑或清理夹具；这不是原定 GLM 的固定 SHA 补审，也不是安全激活批准。

## 重现条件与提交检查

历史执行使用本 checkout 的 `.venv\Scripts\python.exe` 和工作区外的探针，设置 `QA_REPO` 为此 checkout、`QA_EXPECTED_SOURCE` 为上述固定 SHA、`QA_OUTPUT` 为 checkout 父目录下的 `output\qa\030-toctou-20260928`。脚本强制校验 Windows、导入模块路径、源码 SHA、关闭门禁及输出目录范围；每次执行另建全新 Temp 根。

复跑前应在隔离 checkout 核对这五份源码/依赖绑定，另选全新输出目录，按实际固定提交设置 `QA_EXPECTED_SOURCE`，保留历史产物。仅换一个 HEAD 字符串、未核对实现或依赖，不能称为同一基线的复现。本次没有为文件尾空行变更重新执行诊断。

这次提交只包含诊断归档、证据与台账。已检查四组结果的一致性、源码哈希、脚本 AST、JSON 字节复制和关闭门禁。Windows 缺少 `make`，上一笔证据提交已实际遇到 `make: command not found`；本次证据提交采用 `SKIP_PRECOMMIT=1`，没有宣称默认 hook、`make all` 或前端构建通过，未纳入仍失败且未跟踪的 UI RED 测试。不得把证据提交当作产品安全修复交付。

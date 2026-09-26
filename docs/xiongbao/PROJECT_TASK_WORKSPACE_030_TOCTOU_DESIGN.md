# 030A · 路径检查-使用竞态（TOCTOU）与重解析点激活风险安全设计（仅设计）

状态：**设计文档，不含任何生产代码或测试改动。** 固定基线 `f56c33472074bd1399cd94a3e5bc867ed8ca22e6`；本文件是该基线上唯一变更。`PROJECT_TASK_FILES_MODE_ENABLED = False`（`src/octop/infra/projects/file_tasks.py:22`）保持不变；普通 Agent 行为与功能开关不在本设计内改动。本设计**不是**完整 OS 沙箱方案，也不得被引用为沙箱证明。

**给 Codex / GLM 的决策摘要：**

1. **最小可实现策略（本文选定，代号 B+）**：纯 Octop 代码的 fail-closed 拒绝策略——路径逐组件 lstat 拒绝符号链接/junction/非常规文件、列举类操作要求有界“无重解析点子树”证明、托管根建立与启动验证要求真实目录、工具调用边界增加路径否决。Windows 与 Linux 同一套语义，无新平台 syscall 面，无自定义 backend，无依赖变更。它**确定性地**消灭“持久植入链接”攻击类（两个表面、两个平台），并满足合同“禁止不安全入口”这一约定补救（`PROJECT_TASK_WORKSPACE_030_CONTRACT.md` 访问与工具边界节）。
2. **B+ 不能做到的**：它不是 use-time（打开时刻）证明。检查与安装依赖内部再打开之间仍存在瞬时替换窗口；该窗口的利用者必须能在托管根内并发创建/交换重解析点，即**同 OS 账户本地进程**——合同已明确将其排除在 030A 隔离声明之外。使用时刻证明（策略 A）在工具表面必须替换已安装 `FilesystemBackend` 的 resolve→open 序列 ⇒ **自定义 backend 或上游依赖变更**，按本任务规则如实声明并保持**激活 NO-GO**。
3. **激活判定建议**：本设计交付后，`files` 模式激活仍为 **NO-GO**，直至 (a) Codex/GLM/所有者显式接受 B+ 的残余竞态位于“同账户排除”范围内并作为合同补救，(b) 其余独立门禁全部达成（真实 PostgreSQL 配额并发、已认证浏览器全旅程、真实 provider 工具可见性、完整重解析点矩阵、WorkBuddy UI 验收），或 (c) 另行批准自定义 backend / 上游 no-follow 切片。精确缺失原语见 §5.4、§6.5。

---

## 0. 摘要（中文）

030A 私有文件任务当前有三层**检查时刻**路径防线（HTTP 严格解析器、`BackendWorkspace` 相对路径检查、已安装 `FilesystemBackend` 的 resolve-first 包含证明），但合同与 GLM P3 #1 指出的激活阻断项是**检查到打开之间的竞态**：`Path.resolve()` 只是检查时刻包含证明；已安装依赖仅对 POSIX 上**部分最终文件**使用 `os.open(O_NOFOLLOW)`（use-time、仅末组件），父路径组件、`ls/glob/grep` 列举与 Windows 全部操作没有 use-time 防护。本设计给出：完整主体与威胁模型、六工具 + 全部 owner-private HTTP 入口的逐操作 trace 与窗口标注、两个具体策略（A：handle 相对/no-follow use-time 操作；B+：更窄的 fail-closed 拒绝策略）的对比、选定 B+ 的精确模块/类接缝与逐操作可证伪验收表、确定性 RED 测试计划（含检查-使用接缝处的确定性替换钩子与非确定性压测的区分）、以及 rollout/rollback 与不随本设计核销的独立门禁。工具侧 use-time 证明必须替换安装依赖内部打开路径 = 自定义 backend，按任务规则声明后**激活保持 NO-GO**；HTTP 侧 use-time 证明可仅用 Octop 代码达成（Linux 完整、Windows 内容读写可达成、句柄级枚举不可达成），但单独不足以激活。

---

## 1. 证据分级与来源

本文每个事实断言使用下列标签。评审者应把非 `[CODE]` 标签的断言按其等级折价。

| 标签 | 含义 |
|------|------|
| `[CODE]` | 本会话在固定基线工作树中直接读取的仓库代码/测试（附 `path:line`） |
| `[DOC]` | 仓库内既有验收/审查文档记录（附文件名） |
| `[DEP-TASK]` | 任务书提供的已安装依赖事实。**本会话无法读取安装源**（工作树无 `.venv`；`python3`/`uv run`/WebFetch 均被权限拒绝）——按任务指示采用并标注缺失源 |
| `[DEP-TEST]` | 仓库测试对已安装依赖行为的实证（测试即证据，但未读依赖源码） |
| `[PLATFORM]` | 公认操作系统/CPython 平台事实（本会话未执行验证；实现时按 §7.5 IT-2 复核） |
| `[PROPOSAL]` | 本设计提议的新行为（尚不存在） |
| `[UNVERIFIED]` | 未验证假设，实现前必须核实 |

**关键来源清单：**

- 版本钉住：`deepagents 0.7.9`（transitive-only）`[CODE uv.lock:858-872]`；`harness-agent 1.0.14` `[DOC M0_ACCEPTANCE]`；Python 3.12+ `[CODE AGENTS.md §3]`。
- 检查时刻解析器：`resolve_project_task_workspace_path` 用 `Path(root).expanduser().resolve()` + `(root/rel).resolve()` + `relative_to` 包含证明 `[CODE src/octop/infra/backend/project_task_file_paths.py:58-78]`。
- HTTP 严格入口：`project_task_file_io_path` 拒绝 `\\`、`//`、混合 UNC、`from_workspace=False`，再委托解析器 `[CODE src/octop/api/common/workspace.py:160-188]`。
- 依赖行为（任务书事实）：已安装 `FilesystemBackend` resolve-first；POSIX 上对**部分最终文件**用 `os.open(O_NOFOLLOW)`；父路径/列举/glob/grep 与 Windows 存在缺口 `[DEP-TASK]`。**缺失源标注：本会话未能读取 `deepagents/backends/filesystem.py` 安装副本；逐方法防护覆盖度为 `[UNVERIFIED]`，由 §7.5 IT-1 在实现切片开工前补齐。**
- 依赖行为（仓库测试实证）：backend 拒绝 `..` 遍历（ValueError）`[DEP-TEST tests/unit/backend/test_project_task_file_paths.py:151-161]`；拒绝宿主绝对路径读 `[:164-176]`；POSIX symlink 逃逸读 → ValueError `[:179-191]`；Windows junction 逃逸读 → 拒绝或无 canary `[:194-210]`；`BackendWorkspace` 相对遍历 → PermissionError `[:213-224]`；POSIX 宿主绝对回退**能读到 canary**（因此必须由 Octop 解析器先拒）`[:227-241]`；`BackendWorkspace` symlink 逃逸 → PermissionError `[:244-257]`。
- 已认证 HTTP junction 拒绝（真实 NTFS junction、fail-loud）`[CODE tests/integration/test_project_task_files_workspace.py:348-462]`；POSIX symlink HTTP 拒绝含写入无外侧副作用 `[:299-345]`。
- glob 无 backend 内部包含证明（仓库自注）：`"globs have no containment proof inside the backend glob itself"` `[CODE src/octop/api/routers/workspace.py:582-585]`。
- GLM P3 #1（激活阻断项）：解析器检查时刻证明后 backend 以原始相对片段重新打开；“Required before activation: no-follow opens or refusing unsafe entries” `[DOC PROJECT_TASK_WORKSPACE_030_NEXT_GLM_REVIEW.md:56-57]`。
- 合同 TOCTOU 条款：“若发现 TOCTOU，可用 no-follow 打开或禁止不安全入口，不能把单次路径规范化说成完整 OS 沙箱。OS 上同账户的其他进程访问不属于 030A 所声称的隔离。” `[DOC PROJECT_TASK_WORKSPACE_030_CONTRACT.md 访问与工具边界节（L29）]`。
- 仓库内 no-follow 先例（023A 项目资产）：`_ensure_private_dir`（POSIX `O_DIRECTORY|O_NOFOLLOW` 探针 + fstat + fchmod；Windows lstat 真实目录 + `is_symlink`/`is_junction` 拒绝）、`_open_posix_object_no_follow`（逐级 dir_fd 行走）、`open_regular_file_no_follow`（lstat → 打开 → fstat `(st_dev, st_ino)` 同一性比对）`[CODE src/octop/infra/projects/asset_storage.py:108-183]`。
- 工具边界中间件（六工具白名单 + 伪造调用否决；当前**不检查路径参数**）`[CODE src/octop/infra/agents/project_task_file_boundary.py:84-142]`；`write_file` 实参形状 `{"file_path": …, "content": …}` `[DEP-TEST tests/unit/agents/test_project_task_file_boundary.py:347]`（其余五工具实参模式 `[UNVERIFIED]`，见 IT-1）。
- 默认拒绝 HTTP 门禁与 owner-only 允许清单（内部 runtime 的全部对外面）`[CODE src/octop/api/middleware/project_task_file_gate.py:9-19, 96-116]`。
- 托管根布局与创建：`~/.octop/project-task-files/<agent_id>`，不安全 id 拒绝，`mkdir(parents=True, exist_ok=True, mode=0o700)` + POSIX chmod 0700 `[CODE src/octop/infra/utils/paths.py:100-129]`。**注意：`exist_ok=True` 不验证既存目录是否为真实目录（junction 顶替根路径可静默通过）——本设计 §6.1 S5 修复。**
- 启动严格验证：backend 类名比较、`cwd==root`、`virtual_mode`、无 `execute`、中间件精确类核对、spec 相等 `[CODE src/octop/infra/agents/manager.py:989-1073]`；补偿 `shutil.rmtree(root)` `[:1082-1120]`；HTTP 回退工作区构造 `[:3287-3295]`。
- 聊天轮预检（拒绝附件/客户端 picks/模型覆盖；要求既有绑定 thread）`[CODE src/octop/api/routers/chat/turn.py:231-304]`；聊天附件路由 `/agents/{id}/upload` 不在允许清单 → 内部 runtime 被门禁拒绝 `[CODE uploads.py:59 + project_task_file_gate.py:96-116]`。

---

## 2. 主体与威胁模型

### 2.1 主体

| # | 主体 | 能力 | 信任级别 |
|---|------|------|----------|
| P1 | **不可信模型**（runtime 内的 LLM；可被任务内容、项目指令注入、读到的文件字节提示注入） | 仅可调用六工具（边界中间件在执行入口否决伪造调用 `[CODE boundary.py:107-142]`）；可用 `write_file/edit_file` 在根内创建/覆盖**常规文件**；可任选相对路径字符串（含 `..`、通配符）。**不能**经六工具或任何允许入口创建符号链接、junction、硬链接、FIFO、套接字（无此工具面；无 shell） | 敌对 |
| P2 | **远程已认证 API 调用者——所有者本人** | 门禁允许清单上的 11 类路由（tree/file 读写/download/doc 读写/upload/glob/grep/preview/threads 历史）；可写入任意字节到根内常规文件。**不能**创建重解析点（无端点；mkdir/delete/move/archive 对内部 runtime 全员拒绝 `[CODE routers/workspace.py:45-56]`；聊天附件被 turn 预检与门禁双重拒绝） | 半信任（对自己的根有完全意图，但不能越出） |
| P3 | **其他用户/管理员/`as_user`/项目成员/分享读者** | 内部 runtime 的默认拒绝门禁：非 owner 一律 403，管理员与代入亦拒 `[CODE project_task_file_gate.py:167-188]`；分享卡片不含文件 | 敌对（越权面） |
| P4 | **同 OS 账户的其他本地进程** | 可在根内植入/交换 symlink、junction、硬链接、FIFO；可**直接**读写 `~/.octop` 全部字节（包括本根、数据库、其他任务根） | 合同明确排除在 030A 隔离声明之外 `[DOC CONTRACT L29]` |
| P5 | **其他 OS 账户的本地进程** | POSIX：根 0700 且随建随 chmod `[CODE paths.py:123-129]` → 无法进入；Windows：根位于用户 profile 下，继承默认 ACL `[UNVERIFIED——Windows ACL 实测未做]`。若部署把 `~/.octop` 放到宽松 ACL 的共享存储，P5 退化为 P4 | 敌对（应被 OS 权限挡住） |

**关键推论（诚实陈述）**：能制造“检查-使用交换”的主体只有 P4（P1/P2 无法创建重解析点，P3 无入口，P5 应被 OS 权限挡住）。而 P4 已经可以直接读取服务器进程能读的一切——TOCTOU 对 P4 **不产生权限增益**（confused-deputy 的收益上限 = P4 已有的同账户文件系统权限）。因此本竞态的实际安全意义是：(i) 防止**部署假设被打破**时（ACL 配置错误、共享 `~/.octop`、未来 030B 用户自选目录复用同一代码路径）检查时刻防线被绕过；(ii) 满足合同的显式激活条款（no-follow 或禁止不安全入口）；(iii) 把“持久植入”变为确定性拒绝，使植入行为可测、可审计、可 fail-closed，而不是依赖窗口输赢。

### 2.2 030A 能声明与不能声明的

**B+ 落地后能声明（可证伪，见 §6.3 验收表）：**

- C1 非 owner（含管理员/`as_user`/分享读者）对内部 runtime 的全部 HTTP/WS 面默认拒绝。
- C2 路径形状包含：宿主绝对/驱动器/UNC/`file://`/`~`/`..`/NUL/编码遍历形状在触达 backend 前被拒（两平台）。
- C3 **确定性重解析拒绝**：任何已存在的 symlink/junction 出现在操作路径的任一**字面**组件（含根本身）⇒ 该操作被拒（HTTP 403 internal / 工具 error ToolMessage），零外侧字节服务、零外侧副作用（两平台）。
- C4 列举类操作（`ls`/tree/glob/grep）要求请求基子树在检查时刻**无重解析点**（有界扫描，超限 fail-closed 拒绝），否则整体拒绝。
- C5 内容操作的最终组件必须是常规文件或目录（FIFO/套接字/设备文件拒绝——POSIX；Windows 对应非常规属性拒绝）。
- C6 托管根创建与启动验证证明根是**真实目录**（非 junction/symlink 顶替）。
- C7 删除/补偿（`rmtree`）不跟随植入链接（ junction 只删链接本身；symlink 不递归）`[PLATFORM CPython shutil 文档：rmtree 不跟随 symlink；Windows junction 自 3.8 起按链接删除不进入目标]`。
- C8 工具面仍仅六名字，伪造调用执行前否决（既有，不回退）。

**不能声明（激活评审必须原样引用）：**

- N1 **Windows 上无 use-time 竞态免疫**：Python stdlib 在 Windows 无 `O_NOFOLLOW`、无 `dir_fd` 家族 `[PLATFORM os.supports_dir_fd 在 Windows 为空]`；已安装 backend 按路径打开（跟随重解析）。B+ 的检查同样是检查时刻。
- N2 **POSIX 上父组件与列举无 use-time 免疫**：依赖仅对部分最终文件 `O_NOFOLLOW` `[DEP-TASK]`（具体哪些方法 `[UNVERIFIED]`）；父目录组件替换、`ls/glob/grep` 行走替换仍是窗口。
- N3 瞬时替换竞态（P4 在检查与打开之间并发交换）未被消灭，仅被收窄为“必须赢得 OS 级竞态且逐操作重复赢”，且利用者被限定为合同排除的 P4。
- N4 不是 OS 沙箱；不隔离 P4；不覆盖硬链接内容的**语义**防护为可选项（§6.1 S7，默认关）。
- N5 重解析点矩阵不完整：本设计测试目录 junction、目录/文件 symlink；卷挂载点（Volume Mount Point）、GUID 卷路径、WSL drvfs/9p 跨界链接、NTFS 压缩/加密属性交互未测（属独立门禁“完整重解析点矩阵”）。
- N6 已安装依赖逐方法内部行为未读源确认（IT-1 前置义务）。

---

## 3. 现状 trace：从原始输入到最终 OS 操作

### 3.1 强制层栈（两个表面共用后半段）

```
HTTP 面:  raw query/body
  L1 门禁中间件（默认拒绝 + owner-only + 允许清单）        [CODE gate:96-188]
  L2 路由内 owner/root/workspace 钉扎证明                  [CODE routers/workspace.py:59-108]
  L3 project_task_file_io_path → normalize + resolve 包含证明（检查时刻 #1）
                                                            [CODE api/common/workspace.py:160-188;
                                                             infra/backend/project_task_file_paths.py:26-78]
  L4 BackendWorkspace.a* 相对路径检查（检查时刻 #2）        [DEP-TEST unit:213-257]
  L5 FilesystemBackend.resolve-first 包含证明（检查时刻 #3）[DEP-TASK + DEP-TEST unit:151-210]
  L6 最终 OS 打开/枚举（use-time 防护仅 POSIX 部分最终文件 O_NOFOLLOW）[DEP-TASK]

工具面:  model tool_call(args)
  M1 边界中间件名字白名单 + 伪造否决（**当前不看路径参数**）[CODE boundary.py:107-142]
  M2 deepagents 工具函数 → FilesystemBackend.<method>(相对片段)
  L5、L6 同上（工具面没有 L3/L4 等价物！）
```

**窗口定义**：W-HTTP = L3 与 L6 之间（含 L4/L5 两次再检查，每次再检查自身又开新的小窗口）；W-TOOL = M2 进入 backend 后 L5 与 L6 之间。所有 resolve/lstat 检查都是检查时刻；唯一 use-time 元素是 POSIX `O_NOFOLLOW` 最终打开（覆盖方法未验证）`[DEP-TASK][UNVERIFIED]`。

### 3.2 工具面逐操作 trace（六工具）

实参名以安装版 schema 为准；`write_file` 已有 `file_path` 证据 `[DEP-TEST boundary 测试:347]`，其余 `[UNVERIFIED→IT-1]`。最终 OS 操作按依赖任务事实与仓库测试推断，标注等级。

| 工具 | 原始输入 | Octop 现有检查 | 依赖内部（推断） | 最终 OS 操作 | 依赖 use-time 防护 | 窗口 |
|------|----------|----------------|------------------|--------------|--------------------|------|
| `ls(path)` | 相对片段 | 仅名字白名单（无路径检查） | resolve-first 包含 `[DEP-TASK]` | 目录枚举 + 逐条目 stat（跟随性 `[UNVERIFIED]`） | 无（列举缺口 `[DEP-TASK]`；Windows 全无） | L5→枚举、逐条目 stat |
| `read_file(path)` | 相对片段 | 仅名字白名单 | resolve-first；POSIX 部分最终文件 `os.open(O_NOFOLLOW)` `[DEP-TASK]` | `open`+`read` | POSIX 末组件（若此方法在“部分”之列 `[UNVERIFIED]`）；Windows 无 | L5→open（POSIX 父组件仍开） |
| `write_file(path,content)` | 相对片段+字节 | 仅名字白名单 | resolve-first；创建父目录与否 `[UNVERIFIED]`；O_NOFOLLOW 覆盖 `[UNVERIFIED]` | `open(O_WRONLY\|O_CREAT…)`+`write` | 同上未验证 | L5→open/create；父目录 mkdir 序列 |
| `edit_file(path,old,new)` | 相对片段+串 | 仅名字白名单 | 读-替换-写；原子性（temp+rename 还是就地 truncate）`[UNVERIFIED]` | 至少两次打开（读、写） | 同上未验证 | 读窗口 + 写窗口（两个独立 check→use） |
| `glob(pattern,path)` | 通配模式+基路径 | 仅名字白名单 | resolve-first；**glob 行走无包含证明** `[CODE routers/workspace.py:582-585 自注]` | 递归枚举 + fnmatch（`**` 是否进入 symlink 目录 `[UNVERIFIED]`） | 无 | 整个行走 |
| `grep(pattern,path)` | 正则+基路径 | 仅名字白名单 | 行走 + 逐候选文件读取 | 枚举 + 每文件 `open`+`read` | 逐文件同 read（未验证） | 行走 + 每文件打开 |

**工具面结论**：Octop 目前在工具面对路径**零检查**（白名单只管名字）；唯一防线全在安装依赖内部（三层检查时刻 + 不确定的 POSIX 末组件 use-time）。B+ 的 S4（工具路径否决）是首次把 L3 级严格策略带到工具面。

### 3.3 HTTP 面逐操作 trace（owner-private 允许面）

全部路由先过 L1/L2/L3；下表只列 L3 之后的差异。`[CODE]` 行号均指 `src/octop/api/routers/workspace.py`。

| 路由 | L3 检查（行号） | L4/L5/L6 后端链 | 特殊窗口/备注 |
|------|-----------------|------------------|----------------|
| GET `/workspace/tree` | `_io_path`（:201-202） | `als` → `ls` → 枚举 | 枚举无 use-time 防护；条目路径经 `reanchor_entry_path` 单级重锚（:207-210，防 backend 长路径回显） |
| GET `/workspace/file` | `_io_path`（:235） | `aread_text` → `read` | POSIX 末组件 O_NOFOLLOW 可能生效 `[DEP-TASK/UNVERIFIED]` |
| PUT `/workspace/file` | `_io_path` 先于内容构造（:255，403 优先于 404 注释在案） | `aupload_bytes` → `write` | 新文件创建路径：L3 时最终组件可不存在；交换为 symlink 后写穿 |
| GET `/workspace/download` | `_io_path`（:419）；内部 runtime 永不进宿主绝对分支（:421-430） | `adownload_bytes` → read | 同 read |
| GET/PUT `/workspace/doc` | `_io_path`（:459/:493）+ 根不可改（:494-495） | `adownload_bytes`/`aupload_bytes` + DocConverter（内存内转换，无额外 FS 面） | 同 read/write |
| POST `/workspace/upload` | 客户端可控 multipart 文件名先过 `_io_path` 再缓冲字节（:390-394） | `aupload_bytes` → write | 同 write |
| GET `/workspace/glob` | 基路径 `_io_path`（:580）**且** pattern 也过严格解析器（:585） | `aglob` → glob | 含通配符的 pattern 无法逐组件 lstat（B+ 用列举证明补，见 S3） |
| GET `/workspace/grep` | `_io_path`（:620）；pattern 是正则非路径 | `agrep` → grep | 行走+逐文件读 |
| GET `/media/preview` | `_internal_preview_source`：宿主绝对/`file://` 即使指向根内也拒（:92-108）；跨 agent URL 拒绝（:527-542） | `resolve_preview_payload` 仅走 workspace-download 分支 → `adownload_bytes` | 同 read |
| POST `/agents/{id}/upload`（聊天附件） | **门禁拒绝**（不在允许清单）+ turn 预检拒绝附件（turn.py:288-292） | 不可达 | 无 OS 操作 |
| mkdir/delete/move/archive(±) | `_refuse_if_internal_runtime` 全员拒绝（:300,331,361,643,669） | 不可达 | 无 OS 操作 |
| threads/history/read | 门禁允许 + turn/history 处理器内既有 thread 归属校验 | 消息数据（工具结果可含模型经工具读到的文件内容 → 工具面覆盖） | 无独立 FS 面 |

### 3.4 生命周期操作

| 操作 | 现有行为 | 窗口/缺口 |
|------|----------|-----------|
| 根创建 `ensure_project_task_file_runtime_dir` | `mkdir(parents=True, exist_ok=True, 0o700)`+chmod `[CODE paths.py:123-129]` | **`exist_ok=True` 接受既存 junction/symlink 顶替根**；随后 `assert_project_task_file_workspace` 比较双方 `resolve()`——两边同穿链接 ⇒ 相等 ⇒ 通过 `[CODE api/common/workspace.py:147-157]`。检查时刻都一致地被顶替欺骗（B+ S5 修复） |
| 启动验证 `_verify_project_task_runtime` | `cwd==root`（双 resolve）、类名、spec 相等 `[CODE manager.py:989-1073]` | 同上：resolve 相等不证明真实目录（B+ S6 增加 lstat 真实目录核对） |
| 补偿/整任务删除 | `shutil.rmtree(root)` `[CODE manager.py:1104-1113]` | rmtree 不跟随 symlink/junction `[PLATFORM]`；B+ 补确定性测试 RD-6 |

---

## 4. 竞态解剖

1. **`Path.resolve()` 证明什么**：在调用瞬间，把路径中每个符号链接/junction 展开后的规范绝对路径包含于同样瞬间解析的根之下。它**不**把证明绑定到之后的任何打开动作；两次 `resolve()`（L3 与 L5）之间、L5 与 L6 之间都可交换组件。单次或多次规范化都不消除竞态——合同原话即此 `[DOC CONTRACT L29]`。
2. **替换类别**：
   - **持久植入**（链接在检查前已存在并留着）：现有 L3/L5 resolve 已确定性拒绝（仓库测试与已认证 HTTP junction 用例即此类 `[CODE]`）。B+ 将其扩展到工具面（现在工具面只有 L5 一层）并把“根本身被顶替”“列举行走穿链接”“非常规最终文件”三个剩余类别也变成确定性拒绝。
   - **瞬时替换**（check→use 之间换上、事后换回）：任何纯检查时刻策略（含 B+）都无法消灭。消灭需要 use-time 原语（§5.1）。利用者只能是 P4。
3. **依赖的末组件防护语义**：POSIX `os.open(path, …, O_NOFOLLOW)` 在**最终**组件为 symlink 时以 ELOOP 失败——这是真 use-time 末组件防护；但父组件 symlink/junction 仍被内核正常穿越，且 Windows 无此 flag `[PLATFORM]`。任务事实说“部分最终文件”使用了它，具体方法覆盖 `[UNVERIFIED→IT-1]`。
4. **Windows junction 特性**：目录型重解析点，`mklink /J` 无需特权（文件 symlink 需特权或开发者模式）`[CODE tests/integration/...:351-371 注释+实践]`；`os.path.realpath`/`Path.resolve()` 会展开 junction；`Path.is_junction()`（3.12+）与 `os.lstat().st_reparse_tag` 可无跟随检测 `[PLATFORM，IT-2 复核]`。因此 Windows 的现实植入原语 = 目录 junction（不需要任何特权），B+ 检测手段完备，use-time 打开防护缺失。
5. **非重解析植入物**：
   - **硬链接**（同卷常规文件）：lstat 呈常规文件、resolve 包含成立、打开成功 ⇒ 现有三层全不拦。创建者只能是 P4（六工具与 HTTP 面都不能建链接）。可选强化 S7：内容操作最终组件 `st_nlink > 1` 拒绝（根内正常文件 nlink==1；误报面=0，因为没有任何允许入口能产生 nlink>1）。默认**开**（廉价、确定性、无正常功能损失），列为决策点供 Codex 复核。
   - **FIFO/套接字/设备文件**（POSIX，P4 植入）：read 可无限阻塞（DoS）或读到非文件语义数据。S2 的最终组件类型核对（仅常规文件/目录）确定性拒绝——与 `asset_storage.open_download` 先例一致 `[CODE asset_storage.py:281-302]`。

---

## 5. 策略对比

### 5.1 策略 A —— use-time handle 相对 / no-follow 操作

**原语可得性（决定可行性边界）：**

| 原语 | Linux | Windows | stdlib? |
|------|-------|---------|---------|
| 逐级 dir_fd no-follow 行走（`os.open(name, O_RDONLY\|O_DIRECTORY\|O_NOFOLLOW, dir_fd=fd)`） | ✅（仓库先例 `_open_posix_object_no_follow` `[CODE asset_storage.py:133-148]`） | ❌ `os.supports_dir_fd` 为空 `[PLATFORM]` | stdlib(POSIX) |
| 末组件 `O_NOFOLLOW` 打开 | ✅ | ❌ 无此常量 `[PLATFORM]` | stdlib(POSIX) |
| dir_fd 相对 `os.replace/os.rename/os.mkdir/os.unlink/os.stat` | ✅ | ❌ | stdlib(POSIX) |
| 句柄绑定枚举（枚举已验证 fd/handle 本身） | Linux：`os.scandir` 无 dir_fd `[PLATFORM]` ⇒ 需 `/proc/self/fd/N` 魔法链接重开（Linux 专属；无 /proc 的 POSIX ⇒ fail-closed 拒绝） | ❌ Win32 无句柄级枚举 API（`FindFirstFileW` 按路径）；唯一途径 `NtQueryDirectoryFile`（ntdll，ctypes 手工结构体）`[PLATFORM]` | 超出“有界安全补丁” |
| 打开后 use-time 包含证明（先打开→取真实最终路径→比对根） | 可用 `os.readlink(/proc/self/fd/N)`（Linux） | ✅ `msvcrt.get_osfhandle(fd)` + `ctypes.kernel32.GetFinalPathNameByHandleW`（文档化稳定 Win32 API；stdlib ctypes）`[PLATFORM]` | stdlib |
| 打开后同一性证明（lstat 前像 vs fstat `(st_dev,st_ino)`） | ✅ | ✅（CPython 以 GetFileInformationByHandle 填充；仓库先例已跨平台使用 `[CODE asset_storage.py:151-183]`） | stdlib |

**A-HTTP 变体**（仅 Octop 代码，内部 runtime 的 HTTP 面绕过 `BackendWorkspace`，改用自有安全 I/O 层）：
- Linux：完整闭环可达（dir_fd 行走 + O_NOFOLLOW 末组件 + dir_fd 相对 replace 的原子编辑 + `/proc/self/fd` 句柄绑定枚举）。规模估计：新 `infra/backend/` 模块 ~300-400 行 + 全矩阵测试。
- Windows：内容读写闭环可达（open-then-verify：打开→`GetFinalPathNameByHandleW`→包含证明→再读/写；创建先 `O_CREAT|O_EXCL` 空文件→验证→写入，验证失败则删除刚建的空文件并拒绝——外侧空文件瞬现是植入者已赢窗口的前提，属可接受最小副作用，测试断言其被清除）；**枚举（tree/glob/grep 行走）闭环不可达**（需 NtQueryDirectoryFile，超出有界补丁 ⇒ 该三操作在 Windows 只能停留在 B+ 语义或整体拒绝）。
- **致命局限**：工具面不经过 HTTP 层。模型的 `read_file/write_file/edit_file/ls/glob/grep` 直达安装 backend。A-HTTP 单独实施 ⇒ 激活无意义（竞态面主入口是模型工具，不是 owner 自己的 HTTP）。

**A-TOOL 变体**（工具面 use-time）：resolve→open 序列在安装依赖内部，Octop 的可及接缝只有 backend spec 与调用前中间件（中间件在 handler 运行**前**否决=检查时刻；handler 运行**后**压制 ToolMessage ≠ 未读取——字节已入进程内存/可能已落日志，写副作用已发生，不构成 use-time 证明）。因此 A-TOOL 必然要求：
- **自定义 backend**：子类化/替换 `FilesystemBackend`，重写 `ls/read/write/edit/glob/grep` 为安全打开实现，并保持 harness 工具工厂所需的接口。连带影响：`_verify_project_task_runtime` 的类名核对 `[CODE manager.py:1013-1017]` 与 spec `type:"filesystem"` 钉扎 `[:1070-1073]`、`files_backend_constructible` 的 `resolve_backend` 通路 `[CODE file_tasks.py:49-76]` 都要改；`HarnessAgentConfig.backend` 是否接受实例/自定义类型 `[UNVERIFIED]`。**这就是任务规则定义的“需要自定义 backend”情形 ⇒ 如实声明，激活保持 NO-GO。**
- 或**上游变更**：deepagents 增加 root-fd/no-follow 模式（POSIX）与句柄验证模式（Windows）。这超出“有界安全补丁”（是功能级上游改动 + 上游接受周期）⇒ 同样 NO-GO 声明。

**A 的精确缺失原语（NO-GO 声明用）**：*在已安装 `FilesystemBackend` 执行实际打开的调用点处，(i) POSIX：相对钉扎根描述符的逐级 `openat(O_NOFOLLOW)` 行走；(ii) Windows：句柄相对 no-follow 打开与句柄绑定枚举（`NtCreateFile`/`NtQueryDirectoryFile` 级）。两者都无法从 Octop 侧注入该调用点，且 Windows 版在 Python stdlib 中不存在。*

### 5.2 策略 B+ —— 更窄的 fail-closed 拒绝策略（选定）

规则（全部为 Octop 代码、纯 lstat/stat 级、双平台同一实现）：

- **S1 逐组件拒绝**：对操作相对片段的每个**字面**组件做无跟随检测（POSIX：`os.lstat` + `S_ISLNK`；Windows：`st_reparse_tag != 0` 或 `is_symlink()/is_junction()`）；任一组件是重解析点 ⇒ `ProjectTaskPathError` ⇒ HTTP 403 internal / 工具 error ToolMessage。
- **S2 最终组件类型核对**：内容操作（read/write/edit/download/doc/preview/upload）最终组件若存在，必须是常规文件（POSIX `S_ISREG`；Windows 无目录/重解析属性）；目录操作必须是真实目录。FIFO/套接字/设备/链接一律拒绝。
- **S3 列举证明**：`ls`/tree/glob/grep 在派发前对请求基子树做**无跟随有界扫描**（`os.scandir` + `entry.is_symlink(follow_symlinks=False)`/`DirEntry.stat(follow_symlinks=False)` + Windows reparse 属性；条目上限如 50 000，超限 fail-closed 503）；发现任何重解析点 ⇒ 整体拒绝。通配 pattern 无法逐组件 lstat 的问题由本子树扫描覆盖（扫描的是基路径子树，pattern 只在其中匹配）。
- **S4 工具路径否决**：边界中间件在名字白名单之后增加路径否决——按 IT-1 钉住的真实 schema 提取各工具路径参数，逐个执行 normalize + resolve + S1/S2（列举类另加 S3）；**无法识别的实参形状 fail-closed 拒绝**（新的伪造面不能因 schema 漂移而放行）。中间件构造时注入 root（`apply_project_task_file_boundary` 已持有 root_dir `[CODE boundary.py:145-180]`）。
- **S5 根建立验证**：`ensure_project_task_file_runtime_dir` 建成后 lstat 验证根为真实目录（非重解析、正确类型；POSIX 另以 `O_DIRECTORY|O_NOFOLLOW` 打开探针 + fstat，复用 `_ensure_private_dir` 语义 `[CODE asset_storage.py:108-130]`——因 utils 不得 import projects `[CODE AGENTS.md §5 硬禁令]`，抽取到新 `infra/utils/safe_dirs.py`，asset_storage 本次**不**重构）。`exist_ok` 命中既存路径时同样验证，失败 ⇒ 抛错 ⇒ 创建流程走既有补偿。
- **S6 启动验证增强**：`_verify_project_task_runtime` 增加根 lstat 真实目录核对（失败 ⇒ False ⇒ 既有补偿路径）。
- **S7 硬链接拒绝（默认开，决策点）**：内容操作最终组件 `st_nlink > 1` ⇒ 拒绝。根内无任何合法产生 nlink>1 的入口，误报面为零；防 P4 硬链接植入在部署假设破裂时变成读通道。
- **S8 删除安全测试化**：不改 `rmtree` 调用（语义已正确 `[PLATFORM]`），补 RD-6 确定性证明。

**B+ 确定性消灭**：持久植入类（含根本体顶替、列举穿链、非常规文件、硬链接[若 S7 开]）——两表面、两平台、可测。
**B+ 残余**：瞬时替换竞态（仅 P4 可利用，合同排除）；Windows 与 POSIX 列举/父组件无 use-time 防护（N1/N2 原样成立）。
**成本**：内容操作 +O(深度) 次 lstat；列举 +O(子树) 次无跟随 stat（有上限）。任务根为个人任务目录，规模小；深扫描按 AGENTS.md“async 内不做阻塞 I/O”规则可入 executor（与现有解析器内联 `resolve()` 的既有模式一致，浅操作可内联）。
**平台覆盖**：S1-S7 只依赖 `os.lstat/os.scandir/stat` 常量与 3.12 的 `is_junction`——Linux 与 Windows 行为一致；不可检测平台 ⇒ fail-closed（§8.3）。

### 5.3 策略 C —— 维持现状仅记录风险：拒绝

合同把 TOCTOU 处置列为激活条款且 GLM 将其列为激活阻断 P3 `[DOC]`；且工具面目前对路径**零** Octop 检查（§3.2），持久植入在工具面只有依赖单层 resolve 防线。C 不满足“no-follow 或禁止不安全入口”任一支。

### 5.4 对比矩阵与选择

| 维度 | A-HTTP | A-TOOL | B+ |
|------|--------|--------|----|
| 消灭瞬时竞态 | HTTP 面：Linux 全部 / Windows 内容读写 | 需要它的正是这里 | ❌（收窄+限定 P4） |
| 消灭持久植入 | ✅ | ✅ | ✅（含工具面、根顶替、列举、非常规文件） |
| 需要自定义 backend / 上游变更 | 否（但不够） | **是** ⇒ 按任务规则 NO-GO | 否 |
| Windows 枚举闭环 | ❌（NTAPI） | ❌（同） | n/a（检查时刻证明 + 拒绝） |
| 新平台 syscall 面 | /proc 魔法链接、ctypes Win32 | 同左 + backend 内部 | **零**（lstat/scandir/stat） |
| 规模 | 中-大 | 大（含验证链改造） | **小**（~5 个文件、纯增量、内部 runtime 分支内） |
| 普通 Agent 影响 | 需隔离 | 需隔离 | **零**（所有接缝只在 internal 分支/中间件内） |

**选择：B+ 作为最小可实现策略立即设计定稿**（实现是后续切片，需 Codex 派单）；A-TOOL 按其前置条件（自定义 backend 或上游变更）**声明激活 NO-GO**；A-HTTP 不作为激活路径（单独无意义），仅在未来 Codex 决定接受自定义 backend 切片时作为其 Windows/Linux 实现参考。

---

## 6. 选定策略的精确接缝与验收

### 6.1 模块/类接缝（全部 `[PROPOSAL]`，本设计不改代码）

| # | 文件 | 变更 | 边界合规 |
|---|------|------|----------|
| S1/S2/S3 | `src/octop/infra/backend/project_task_file_paths.py` | 新增 `assert_plain_components(root, rel, *, wildcard_tail=False)`、`assert_plain_final(root, rel, kind)`、`assert_listing_subtree_reparse_free(base, *, max_entries)`；`resolve_project_task_workspace_path` 末尾默认调用 S1（对既有测试向后兼容：拒绝用例仍抛 `ProjectTaskPathError`，正例树是干净的） | infra/backend，纯 stdlib ✅ |
| S3 调用点 | `src/octop/api/routers/workspace.py`（tree :201-211、glob :567-604、grep :607-625）与 `src/octop/api/common/workspace.py`（`project_task_file_io_path` 保持签名，内部获得 S1/S2） | 列举路由在 `ws.als/aglob/agrep` 前调用列举证明（仅 `root is not None` 分支） | api 薄层调 infra ✅ |
| S4 | `src/octop/infra/agents/project_task_file_boundary.py` | `ProjectTaskFileToolBoundaryMiddleware.__init__(root)`；`_path_veto(request)` 按 IT-1 钉住的 schema 提取路径参数并执行 normalize+resolve+S1/S2（+S3 对 ls/glob/grep）；未识别形状 ⇒ 拒绝；`apply_project_task_file_boundary` 传入 root | infra/agents ✅（manager.py 构造处一行改动） |
| S5 | 新 `src/octop/infra/utils/safe_dirs.py` + `src/octop/infra/utils/paths.py:123-129` | `assert_plain_directory(path)`（lstat 类型+重解析核对；POSIX 加 O_NOFOLLOW 目录探针）；`ensure_project_task_file_runtime_dir` 建成后调用 | utils 仅 stdlib ✅ |
| S6 | `src/octop/infra/agents/manager.py:989-1073` | `_verify_project_task_runtime` 增加根 lstat 真实目录核对（失败→False→既有补偿） | ✅ |
| S7 | 并入 S2 的最终组件核对 | `st_nlink>1` 拒绝（决策点：Codex 可裁撤） | ✅ |

**明确不改**：`PROJECT_TASK_FILES_MODE_ENABLED`；已安装 deepagents/harness 任何文件；门禁允许清单；普通 Agent 分支（`root is None` / 非 internal 行）；被拒 mutators；数据库 schema；`asset_storage.py`（先例仅复用其模式）。

### 6.2 实现时核实义务（先于任何 RED→GREEN）

- **IT-1（缺失源补齐）**：在可运行 venv 的环境读取安装的 `deepagents/backends/filesystem.py`（0.7.9）与 harness 六工具 schema：钉住 (a) 哪些方法用 `O_NOFOLLOW`；(b) ls/glob/grep 的枚举与 stat 跟随性；(c) write 是否创建父目录；(d) edit 的原子性；(e) 六工具真实参数名/必填性。任何与 `[DEP-TASK]` 事实的偏差 ⇒ 回报 Codex，重开本设计对应行。
- **IT-2**：确认目标 Python（≥3.12）上 `Path.is_junction()`、`os.lstat().st_reparse_tag`、Windows `st_ino/st_dev` 填充行为 `[PLATFORM 标签复核]`。

### 6.3 逐操作验收表（可证伪；两平台；“植入”=在所述位置预先创建持久 symlink[POSIX]/junction[Windows，fail-loud helper]）

记号：403i = 403 FORBIDDEN `details.internal=true`；TM-err = 工具返回 error ToolMessage 且 handler spy 未执行；503u = PROJECT_TASK_FILES_UNAVAILABLE；“外侧零副作用” = canary 字节不变 + 外侧无新文件 + （S7/创建类）无残留。

| # | 操作（表面） | 植入形态 | 期望（Linux） | 期望（Windows） | 正例期望 |
|---|--------------|----------|----------------|------------------|----------|
| A1 | `read_file`（工具） | 最终组件 = 链接→外侧文件 | TM-err；canary 不入结果 | 同左 | 干净文件读成功 |
| A2 | `read_file`（工具） | 中间目录组件 = 链接→外侧目录 | TM-err | 同左（junction） | 干净嵌套读成功 |
| A3 | `write_file`/`edit_file`（工具） | 中间或最终组件链接 | TM-err；外侧无新文件/字节不变 | 同左 | 干净写/编辑成功且落根内 |
| A4 | `ls`（工具）/ tree（HTTP） | 基子树内**任意位置**有链接 | TM-err / 403i（S3 扫描命中，即使链接不在请求路径字面组件上） | 同左 | 干净树列举成功；**兄弟子树**有链接不影响本子树列举（防过度拒绝） |
| A5 | `glob`/`grep`（工具与 HTTP） | 基子树内有链接；或 pattern 字面前缀组件是链接 | TM-err / 403i | 同左 | 干净匹配成功；`**` 不穿出根 |
| A6 | 工具实参形状 | 未知/缺失路径参数、绝对路径、`..`、UNC、NUL | TM-err（fail-closed，schema 漂移不放行） | 同左 | 已知形状正常派发 |
| A7 | GET `/workspace/file`、`/download`、`/doc`、`/media/preview`（HTTP，owner 认证） | 最终/中间组件链接 | 403i；响应无 canary | 同左 | 200 + 正确字节 |
| A8 | PUT `/workspace/file`、`/doc`、POST `/upload`（HTTP） | 目标路径经链接指外侧（含“外侧已存在文件覆盖写”——补 GLM P3 #3 缺口） | 403i；外侧文件字节不变；无新建 | 同左 | 200/201 + 字节落根内 |
| A9 | 任一内容操作 | 最终组件 = FIFO/套接字（POSIX）；nlink>1 硬链接（S7，两平台） | 403i / TM-err | 硬链接形态同左（FIFO 不适用，标 skip） | 常规文件正常 |
| A10 | 根创建（生命周期） | 根路径本身 = 预植 junction/symlink→外侧目录 | 创建失败（AGENT_FAILED）+ 补偿；外侧目录不被当作根使用、不被删除 | 同左 | 真实目录 0700/ACL 建立、验证通过 |
| A11 | 启动验证 | 启动后、验证前根被顶替（测试以直接调用验证函数模拟） | `_verify_project_task_runtime`=False ⇒ 补偿 | 同左 | 干净根验证通过 |
| A12 | 整任务删除/补偿 | 根内有链接 | rmtree 只删链接；外侧树完整（canary 在）；根消失 | 同左（junction） | 干净根整树删除 |
| A13 | 列举扫描上限 | 子树条目 > 上限 | 503u（fail-closed，不静默放行） | 同左 | 上限内正常 |
| A14 | 普通 Agent 回归 | 普通 workspace 内有 symlink | **行为不变**（不因 B+ 拒绝；legacy 映射分支零改动） | 同左 | 既有套件全绿 |

每行都是确定性断言（植入持久存在，检查与使用时刻都能看见）——这正是 B+ 的可证伪声明边界；瞬时竞态行不存在于验收表（见 §7.3 分类）。

---

## 7. RED 测试计划

平台标记遵循 AGENTS.md §7 跨平台规则（`posix_only`/`skipif(os.name=="nt")` 约定；junction 用例沿用 `_require_windows_junction` 的 **fail-loud** 惯例——环境建不出 junction 必须 `pytest.fail`，不得静默跳过 `[CODE tests/integration/...:351-371]`）。

### 7.1 确定性单元/集成（门禁级，RED 先行）

- **RD-1 解析器组件拒绝矩阵**（`tests/unit/backend/test_project_task_file_paths.py` 扩展）：S1/S2/S7 对中间组件链接、最终组件链接、FIFO、硬链接、干净正例；A13 上限。
- **RD-2 HTTP 全路由植入拒绝 + 正例**（`tests/integration/test_project_task_files_workspace.py` 扩展，复用 `_ws_ctx/_forbidden/_never_served`）：A7/A8 全部路由 × {最终组件链接、中间组件链接}，断言 403i、canary 缺席、外侧零副作用；随后移除植入断言 200 正例（证明拒绝不是把功能弄坏）。**补 GLM P3 #3**：junction 下外侧**已存在**文件的覆盖写拒绝。
- **RD-3 列举证明**：A4/A5，含“兄弟子树链接不拒绝”防过度拒绝用例。
- **RD-4 工具边界路径否决**（`tests/unit/agents/test_project_task_file_boundary.py` 扩展 + 以真实 harness 工具集构造的集成用例，无 live provider）：A1-A6、A9；handler spy 未执行断言沿用既有模式 `[:230-238]`；实参 schema 以 IT-1 钉住值参数化，另加“未知 schema ⇒ 拒绝”用例。
- **RD-5 根创建验证**：A10（`tests/unit/agents/test_project_task_file_runtime.py` 扩展 + paths 单测）。
- **RD-6 清理不跟随**：A12。
- **RD-7 普通 Agent 不受影响**：A14（既有兼容用例保持 + 新增“普通 workspace symlink 行为不变”显式断言）。

### 7.2 检查→使用接缝的确定性替换钩子（可行范围内）

真实“检查后、打开前”替换在**不打桩生产代码**的前提下可用**测试侧调用序钩子**确定化：monkeypatch `os.open`（POSIX）为包装器——在“L3/L5 检查已完成、目标名首次被打开”这一确定调用点执行替换（常规文件→symlink / 目录→不可行则用预置双目录+rename 交换），再委托真实 `os.open`。断言：POSIX 上安装 backend 的 O_NOFOLLOW 方法以 ELOOP 失败或 B+ 前置拒绝，canary 永不出现。Windows 版：包装 `os.open`/`CreateFileW` 不可直接触达 CRT，退而包装 B+ 自己的 lstat 行走**之后**的 backend 调用入口（`FilesystemBackend.read` 等方法级 patch，方法进入即交换目录↔junction）——文件 symlink 变体在无特权/非开发者模式机器按规则条件跳过并如实标注。这些钩子打桩的是**依赖/stdlib 调用点**（测试专属），不修改任何生产源文件；它们证明的是“交换被 use-time 防护（POSIX O_NOFOLLOW 覆盖处）或 fail-closed 拒绝（B+ 覆盖处）接住”，并**如实记录哪些方法在 Windows 上接不住**（该结果本身是激活决策证据）。

### 7.3 非确定性压测（证据级，永不作门禁）

- **ST-1 并发交换压测**：交换线程在 dir↔junction（Windows）/ dir↔symlink（POSIX）间翻转，工作线程并发跑六工具与 HTTP 面 N 轮；断言“canary 从未出现在任何响应/工具结果/外侧新文件”；输出轮数与命中拒绝计数。归类：非确定性、允许 flake 重跑、不进 ship gate；其价值是量化窗口宽度，不是证明。

### 7.4 正例与外侧零副作用断言（全计划通用）

每个拒绝用例必须伴随：(i) 同 fixture 的干净正例通过；(ii) 外侧 canary 字节逐字节不变；(iii) 外侧目录 `iterdir()` 集合不变；(iv) 根内无意外新文件。沿用既有 junction 用例的 cleanup 证明模式（先 `link.rmdir()` 摘除重解析点、再断言外侧树完整 `[CODE tests/integration/...:450-462]`）。

---

## 8. Rollout / 回滚 / 平台 / 独立门禁

### 8.1 Rollout

1. IT-1/IT-2 核实 → 2. `safe_dirs.py` + 解析器扩展（RD-1 RED→GREEN）→ 3. HTTP 调用点（RD-2/3）→ 4. 工具边界否决（RD-4）→ 5. 根验证/启动验证（RD-5）→ 6. 清理/回归（RD-6/7）→ 7. 7.2 接缝钩子与 ST-1 证据运行 → 8. GLM 固定 SHA 只读复审 + Codex 双平台独立复跑。**每一步都在 internal-runtime 分支内，switch 保持 False；整个 rollout 不改变任何对外可见行为**（模式本就不可创建）。

### 8.2 回滚

单切片提交、无迁移、无配置面、无状态残留 ⇒ `git revert` 即完整回滚。B+ 拒绝语义若造成误拒（唯一现实来源：IT-1 钉错的工具 schema），fail-closed 方向保证误拒是功能不可用而非泄漏。

### 8.3 不受支持平台行为

- 判定：`os.name not in ("posix", "nt")`，或 POSIX 但非 Linux 且 S1 依赖的 lstat 语义不可确认（macOS 未进 CI）⇒ `ensure_project_task_file_runtime_dir`/能力探测 fail-closed ⇒ 创建路径返回既有稳定码 `PROJECT_TASK_FILES_UNSUPPORTED`(422)/`PROJECT_TASK_FILES_UNAVAILABLE`(503)（决策点：具体码由 Codex 定，倾向前者=平台不支持）。
- WSL2 注意：本工作树在 WSL2；ext4 侧语义 = Linux；若 `~/.octop` 被放到 `/mnt/c`（drvfs），symlink/junction 跨界语义未测 `[UNVERIFIED]`——列入独立“完整重解析点矩阵”门禁，不在 B+ 内声明。

### 8.4 不随本设计核销的独立门禁（原样保留）

真实 PostgreSQL 迁移与配额并发；已认证浏览器创建—读写—重启—撤权旅程；真实 provider 下模型可见工具与调用边界；完整 OS 级重解析点矩阵（卷挂载点、GUID 卷路径、文件 symlink 特权形态、drvfs/9p）；WorkBuddy UI 验收与 25 项总矩阵、PS-08。GLM 其余 P3（类名核对 P3 #2、前端 `my:file.txt` 不一致 P3 #4、typecheck/Chat 基线红 P3 #5）不属本设计，状态不变。**`PROJECT_TASK_FILES_MODE_ENABLED = False` 贯穿本设计与 B+ 实现切片；翻开关是另行决策。**

---

## 9. 诚实声明与未验证假设汇总

1. 本设计不是完整 OS 沙箱，B+ 落地后也永远不能这样称呼（合同条款）。
2. 安装依赖源在本会话不可达（无 venv、python/网络被权限拒绝）：`FilesystemBackend` 的 resolve-first、POSIX 部分末组件 `O_NOFOLLOW`、父路径/列举/glob/grep 与 Windows 缺口均为 `[DEP-TASK]` 转述 + `[DEP-TEST]` 局部实证；逐方法覆盖度 `[UNVERIFIED]`，IT-1 是硬前置。
3. 六工具中仅 `write_file` 的实参形状有仓库测试证据；其余 schema `[UNVERIFIED]`，S4 以 fail-closed 兜底。
4. `HarnessAgentConfig.backend` 能否接受自定义实例未验证——这只影响被否掉的 A-TOOL 路线，不影响 B+。
5. Windows 根目录 ACL 隔离（P5 阻断）未实测 `[UNVERIFIED]`。
6. `[PLATFORM]` 标签的 CPython/Win32 事实（supports_dir_fd、GetFinalPathNameByHandleW、is_junction/st_reparse_tag、rmtree junction 语义、/proc/self/fd 重开）在实现时以 IT-2 在目标平台复核。
7. B+ 对瞬时竞态**无免疫声明**；其安全论证依赖“植入者=P4=合同排除主体”+“持久植入确定性拒绝”+“依赖自身三层检查仍在”。若 Codex/GLM 认为部署现实（共享 `~/.octop`、未来用户自选目录复用此代码）使 P4 假设不可接受，则结论自动升级为：激活 NO-GO，直至 A-TOOL（自定义 backend 或上游 no-follow）落地。
8. 协作通道说明：本会话对 `channel.py event` 的全部调用（read-context、write-design 两阶段共 5 次尝试）均被本环境权限系统拒绝，未能发布阶段事件；未使用 `ask`（无需澄清的公共行为歧义——两处决策点已作为显式选项写入 §5.2 S7、§8.3 供 Codex 在评审时裁定）。

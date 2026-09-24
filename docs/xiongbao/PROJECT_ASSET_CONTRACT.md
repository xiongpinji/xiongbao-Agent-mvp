# 熊宝-Agent 项目资产合同（PS-06A / 023 草案）

状态：2026-09-24，GLM-5.3 已对初稿完成独立只读设计审查，指出发布顺序、撤权锁和迁移约束的 P0/P1；以下为 Codex 修订后的 023A 实施合同，尚未实施或验收。018–022 的项目域和成员权限是依赖。WorkBuddy 5.5.6 本机只读观察到了资产页的文件夹、上传、筛选、搜索、容量、列表和“添加到任务”入口，官方更新日志提到版本历史；没有在 WorkBuddy 实测上传、下载、版本或内部授权。以下数据与权限是熊宝自己的产品合同，不推断 WorkBuddy 的实现。

## 交付边界

本批先交付 **023A 项目资产基础**：独立私有存储根、文件夹、文件上传、列表、名称搜索、类型筛选、容量展示和登录成员下载。每次上传创建不可变版本记录，为 023B 的版本历史/恢复留好数据模型。023B 再开放版本列表、同名文件新版本、删除/恢复与空间回收。任务引用要等 PS-05B 任务协作者 ACL 定稿后单独接入；023A 页面应将“添加到任务”明确禁用并说明原因。项目资产绝不落入 Agent 工作区、个人资料库、公开静态目录或浏览器可猜测 URL。

023A 不做图片预览、全文索引、外部对象存储、分片续传、批量上传、文件夹移动或客户端本地路径引用。没有实际后端的控件不能显示为可用。文件版本/删除尚未开放时，页面不能暗示已支持。资产列表和下载不因项目成员身份扩展到任何任务私聊、Agent 文件或个人 OAuth 凭据。

## 身份、名称与结构

- 仅当前 `project_members` 中的 owner/admin/member 能列出、搜索、下载、创建文件夹和上传。非成员、已撤权成员、未知项目/节点统一 404；已登录成员对未来受限管理动作可用 403。已归档项目仍允许现有成员列表与下载，禁止新建文件夹和上传（403）；当前尚无归档 UI/API，这一规则防止后继批次分歧。每个列表、下载、版本、旧版本和未来任务引用请求均重复服务端授权；资产 ID、版本 ID 和对象键均不是授权凭证。文件夹节点被调用下载接口时按文件不存在统一 404。
- 一个项目有一个隐藏根文件夹节点。所有可见节点都以 `parent_node_id` 指向同项目文件夹；不能跨项目挂接，不能把文件当父节点。项目创建后的老项目在首次资产操作时幂等初始化根。根节点不能被重命名、移动或删除。
- 同一父文件夹内，名称经过 Unicode NFC 与首尾空白处理后须为 1–120 个字符，`name_key` 固定为该结果的 Unicode `casefold()`，因此同父 `Foo`/`foo` 冲突；不同父文件夹允许同名。拒绝 `/`、`\\`、NUL、控制字符，以及整名等于 `.` 或 `..`；Windows 保留设备名 CON/PRN/AUX/NUL/COM1–9/LPT1–9 按首个点之前的主干不区分大小写拒绝，例如 `CON.txt`。允许 0 字节文件，禁止空文件名。文件名只作为显示元数据，最终对象路径由服务端生成，不拼接用户文件名。
- `project_asset_nodes` 持有 `node_id`、`project_id`、`parent_node_id`、`kind`、`name`、`name_key`、可空的 `created_by`、`created_at`、`updated_at`；`project_asset_versions` 持有 `version_id`、`project_id`、`node_id`、`object_key`、`size_bytes`、`sha256`、`media_type`、可空的 `uploaded_by`、`created_at` 和 `is_current`。`is_current` 为 0/1 且有数据库 CHECK；023A 文件节点只有一个 `is_current=1` 的版本，下载与容量只查当前版本；以两库均支持的 `UNIQUE INDEX (node_id) WHERE is_current = 1` 禁止两个当前版本。023B 新版本在同一事务内翻转标志，旧版本对象字节永不改写。023A 不暴露“上传新版本”。
- 两表须由 023 配对 SQLite/PostgreSQL 迁移建立。节点有 `UNIQUE (project_id,node_id)` 作为复合外键的父侧键、`UNIQUE (project_id,parent_node_id,name_key)` 防同父并发重名，`CHECK (kind IN ('file','folder'))`；唯一隐藏根是每项目唯一的 `parent_node_id IS NULL` 行，以部分唯一索引 `UNIQUE (project_id) WHERE parent_node_id IS NULL` 强制。可见节点的父 ID 必须非空，父节点必须是同项目文件夹；根节点不能被重命名、移动或删除。`project_spaces → nodes`、`nodes → children`、`nodes → versions` 三条外键均 `ON DELETE CASCADE` 且子节点/版本以 `(project_id,node_id)` 复合外键防跨项目挂接；用户外键 `ON DELETE SET NULL`。项目删除仅级联元数据，物理对象由受控回收处理。SQLite 建表成功不代表复合 FK 有效，必须用实际插入子节点的测试验证；PG 实库另验。

## 上传、存储与容量

- 使用 `PathLayout` 下独立的服务端私有项目资产根；最终对象路径仅由服务端版本 ID 导出。私有临时目录与最终对象目录位于同一文件系统，以便原子 `os.replace`；临时文件只允许服务端生成名，POSIX 使用 0600 权限。拒绝符号链接、路径逃逸、空文件名和超出配置上限的文件。上传必须分块写入磁盘临时文件并增量计算 SHA-256/实际字节数，不得把请求文件完整缓冲在内存；`read_upload_capped` 和知识库 `write_document` 的整文件 `bytes` 模式不能照搬。若有 `UploadFile.size`，在 handler 读取前预检超限；`Content-Length` 仅在大于文件上限再加 64 KiB multipart 开销宽限时预拒绝，避免把边界内合法文件误判为 413。FastAPI/Starlette 在 handler 前已可能 spool multipart，023A 不承诺网络请求体中途拒收。前端显示等待态，失败可重试且不得显示半成品。
- 固定上传发布顺序：① 分块写临时文件、校验并关闭；② `os.replace(temp, final)` 原子改名到仅由版本 ID 决定的最终路径（在平台允许时刷盘）；③ **单个数据库事务**先锁成员行，再核对项目非归档与同项目文件夹父节点，插入节点和当前版本并提交；④ 只有提交成功才返回 201。数据库提交是唯一“可见”标志，列表、搜索、容量和下载只读已提交的节点/当前版本；不得先提交元数据再改名，也不需要额外 pending 状态。PG 用成员行 `SELECT ... FOR SHARE`，SQLite 用现有 `db.transaction()` 的 `BEGIN IMMEDIATE`；锁序成员行 → 资产行，与 `ProjectRepo.remove_member` 一致。建文件夹同样在这个事务内检查成员/父节点/归档，防撤权后晚到写入。
- 崩溃恢复四窗：W1 临时写入中、W2 校验后改名前，只有过期 temp、无 DB 行，列表/容量不可见；W3 改名后提交前，只有孤儿 final、无 DB 行，列表/容量不可见；W4 提交后响应前，文件与 DB 均存在、可正常下载，客户端重试同名得到 409。普通异常在改名前只 `unlink` 自己的临时文件，改名后若事务失败只 `unlink` 本次刚创建的 final；禁止递归删除和按用户输入拼路径。受限清理器在服务进程重启后的首次资产操作清理超过 24 小时的 temp 与不被 DB 版本引用的 final，只扫描服务端生成对象模式、只做精确路径 `unlink`，保留较新对象避免误删在途上传，并记录项目/版本/结果摘要；023B 再补定时清理。若 W4 发生，任何清理都不得删除已引用对象。
- 023A 的“容量”只显示已提交文件 `is_current=1` 版本的 `size_bytes` 汇总及文件数，允许 0 字节文件，不显示虚构配额。全局 `max_upload_mb` 是单文件上限；项目配额和历史版本计费在 023B 定规则后实施。文件 MIME 由服务端安全探测或保守使用 `application/octet-stream`，浏览器声明的类型仅作提示；下载响应固定 `Content-Disposition: attachment` 和 `X-Content-Type-Options: nosniff`，阻止用户上传的 HTML 被同源内联执行。
- 下载在返回流之前的同一请求内重新检查项目成员资格、节点/当前版本归属及对象真实路径；已开始传输的流不因后续撤权中断，撤权后旧深链再次请求必须 404。对象由 `lstat` 确认普通文件、拒绝 symlink，解析后的路径必须在私有资产根下；尽可能使用平台的 no-follow 打开原语，不能照搬知识库 `is_file()` 的跟随 symlink 行为。非成员、跨项目、目录节点与对象缺失统一 404。不生成静态对象 URL、公开绝对路径或长期签名链接；下载/列表 JSON 不返回 `object_key`、服务端绝对路径、临时路径或存储凭据。
- 上传/下载不会触发模型或第三方服务；023A 不写 `project_events`，不把文件内容放进项目动态、日志或异常正文。结构化服务端审计日志只记录操作者、项目、节点/版本 ID、时间、结果和字节数，不记录文件正文、原始 multipart、原文件名或本机绝对路径。

## HTTP 与界面合同

| 路由 | 行为 | 关键失败 |
| --- | --- | --- |
| `GET /api/projects/{project_id}/assets?parent_id=&q=&kind=&limit=&offset=` | 当前文件夹直接子节点；`kind` 仅 `file`/`folder`/缺省全部，`q` 用 NFC + casefold 后对 `name_key` 做服务端转义的包含搜索；先过滤再按 `(kind,name_key,node_id)` 全序排序并 `limit/offset` 分页。响应 `{items,total,limit,offset,has_more}`，每项为 `{node_id,parent_node_id,kind,name,size_bytes,media_type,created_at,updated_at}`；文件夹的 size/media 为 null。无 `parent_id` 表示隐藏根 | 非成员/不存在父节点 404；非法参数 422 |
| `POST /api/projects/{project_id}/assets/folders` | `{parent_id?,name}` 创建文件夹，201，返回上述安全节点项；缺省父节点为隐藏根 | 非文件夹父节点 422；同名 409；非成员 404；归档项目 403 |
| `POST /api/projects/{project_id}/assets/upload` | multipart `file` 和可选 `parent_id`；DB 提交成功才返回 201，响应精确为 `{...安全节点项, version: {version_id,size_bytes,sha256,media_type}}`，首版摘要嵌套以避免与节点同名字段冲突，不含对象键 | 超限 413；同名 409；非成员 404；归档项目 403；失败不得留下可见记录 |
| `GET /api/projects/{project_id}/assets/usage` | `{file_count,total_bytes}`，只计已提交当前版本 | 非成员 404 |
| `GET /api/projects/{project_id}/assets/{node_id}/download` | 当前版本的私有流式附件，强制 attachment + nosniff | 非成员、跨项目、撤权、目录节点、文件缺失统一 404；绝不回退到其他项目同名文件 |

错误封套沿用 Octop `OctopError`：新增 `PROJECT_ASSET_NAME_CONFLICT`→409、`PROJECT_ASSET_INVALID`→422、`PROJECT_ASSET_TOO_LARGE`→413，在服务端 `i18n` 中英及 Dashboard `apiErrors` 中英补齐；外人/未知统一复用 `NOT_FOUND`→404，归档成员写操作复用 `FORBIDDEN`→403。不得用裸 `HTTPException` 绕过项目现有错误封套。`limit` 为 1–100，`offset` 为非负整数；023A 接受并发插入时 offset 翻页可能重复，前端去重显示，后续可改游标。资产页不支持子目录 URL 深链：刷新回到根，面包屑由本次会话的导航栈及列表项 `parent_node_id` 构建。

Dashboard 资产页接真实接口，呈现面包屑、文件夹/文件列表、上传、名称搜索、类型筛选、容量、加载/空/失败态；切换项目或目录后不闪现旧资产，晚到请求不可覆盖新项目。owner/member 的真实浏览器上传、下载与刷新必须一致；非成员不能从 API 或旧链接访问。文件名作为纯文本渲染。未完成的版本、删除和添加到任务功能应标明后续批次。

## TDD 与验收门槛

先写失败的迁移/仓库/API/前端行为测试，再实现。至少覆盖 SQLite 新库及 022→023 升级、复合 FK 实际子节点插入、根初始化幂等、同父同名竞争和 `is_current` 唯一、跨项目父节点和节点/版本 ID、非成员与撤权后下载 404、归档项目只读、文件名 `Foo/foo` 与 `CON.txt` 等字符、0 字节/超限上传、Content-Length/declared-size 预检、上传中断和 W1–W4 故障注入不产生可见半成品、超过/未超过宽限期的 temp/final 回收、目录与类型筛选先于分页、相同排序键稳定分页、下载字节与 SHA 一致、attachment/nosniff/路径不泄露及 symlink 拒绝。模拟故障不能替代实际浏览器下载；跨数据库静态 SQL 对照不能替代 PostgreSQL 真实迁移和并发实测。

Codex 独立检查差异与路径白名单，运行受影响后端/前端回归、类型/格式/生产构建，做 1280×768 三身份隔离浏览器旅程，并由 GLM 只读复核资产路径与 ACL 后才推送。023A 完成也不代表 PS-06 或 WorkBuddy 1:1：023B 版本历史/恢复/删除、项目配额与真实存储回收，后续任务引用和视觉逐状态仍单独验收。

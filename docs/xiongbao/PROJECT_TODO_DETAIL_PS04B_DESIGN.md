# PS-04B 项目待办详情、评论与图片设计

状态：**书面规格已于 2026-09-27 获用户批准；B1 已按[验收记录](PROJECT_TODO_DETAIL_PS04B_B1_EVIDENCE.md)交付到 xiongbao/main，B2 实施中**。基于用户批准的连续两片方案：B1 交付双栏详情、旧纯文本兼容和成员文字评论；B2 交付安全 Markdown 富文本与受控评论图片。两片属于同一 PS-04B 目标，第一片不得宣称 WorkBuddy 1:1。

## 依据与边界

WorkBuddy 5.3.3 [官方更新日志](https://www.workbuddy.cn/docs/workbuddy/Changelog)记录待办富文本、评论图片粘贴上传与动态留言；[项目说明](https://cloud.tencent.com/document/product/1831/138797)将“计划事项”和 AI 对话任务区分。本机既有 [WorkBuddy 只读观察](WORKBUDDY_LIVE_UI_AUDIT.md)见到覆盖项目页的双栏待办详情：左侧标题、富文本正文、底部评论，右侧状态、处理人、起止日期、优先级和标签。观察没有验证评论发表、图片上传或所有编辑控件的实际效果；下述数据、权限和失败规则是熊宝自己的设计。

020 [待办合同](PROJECT_TODO_CONTRACT.md)的创建、指派、版本化 PATCH、软删除与表格/看板数据源保持有效。现有 `description` 是最多 4,000 字符的纯文本；022 `project_messages` 是项目动态留言，不等于待办评论，不能改名复用。PS-04B 不依赖 045-F 资产移动前端或 046 资产入任务，也不开放项目内任务发送、对话正文协同或项目公共凭据。

## 两片交付与交互

**B1**：表格行和看板卡打开同一个双栏详情覆盖层。左侧展示标题、现有纯文本描述、按时间排列的评论与文字评论输入；右侧仅提供当前真实可编辑的状态和处理人，沿用 020 版本冲突与角色权限。旧描述中的 `#`、`<...>` 等符号按普通文字展示，不重新解释为 Markdown/HTML。覆盖层在 1280×768 与 800×728 内可独立滚动，输入区不遮挡末条评论；Escape 关闭并将焦点还给触发卡片，键盘可到达所有操作。`/projects/{project_id}?tab=plan&todo={todo_id}` 可直接打开详情；已删、跨项目或撤权后不显示旧标题/评论，给出无权或不存在状态。关闭时移除 `todo` 参数；浏览器后退可回到原计划视图。项目动态只回显安全的“某成员评论了待办”事件，点事件打开该待办，不下发评论正文。

**B2**：左侧描述改为有工具栏与预览的富文本编辑区，限定段落、标题、强调、列表、引用、代码和 HTTP(S) 链接，保存为 Markdown；不接受原始 HTML、脚本、iframe、外站图片或 `data:` 图片。待办正文不插入图片：本片图片只附于评论。评论输入区可粘贴 PNG/JPEG/WebP 图片，先在本地预览；提交时文字与图片一次发送，成功后刷新评论列表，失败时保留草稿和本地预览供手动重试。只读状态、加载/空态、上传进度、过大/不支持格式、失败重试和项目切换后的晚到请求均有明确反馈。图片在评论下方内嵌显示，不自动变成项目资产库条目。

起止日期、优先级、标签、视图字段/分组配置和 WorkBuddy 逐状态像素/键盘对照仍是 PS-04 后续差距；B1/B2 不以空壳按钮暗示它们可用。

## B1 数据与 API

新增 SQLite/PostgreSQL 配对迁移 `project_todo_comments`：不可猜测 `comment_id` 主键、`todo_id` 外键、可空 `author_user_id` 外键、去空白的 `body_text`、`client_request_id`、`request_fingerprint`、`created_at`；索引 `(todo_id, created_at DESC, comment_id DESC)`，活动作者与待办内请求号有唯一约束。数据库允许正文 0–4,000 字符以兼容 B2 的纯图片评论；B1 服务层仍要求至少一个非空文字字符，B2 服务层要求非空文字或至少一张有效图片。账号删除后评论保留，作者显示“已删除用户”；待办软删除后普通入口不再返回评论。迁移编号在实施前对主线重新查询，不预占 031。

`GET /api/projects/{project_id}/todos/{todo_id}/comments?limit=20&cursor=` 返回 `{items,next_cursor}`；每项固定为 `{comment_id,todo_id,author_user_id,author_name,body,images,created_at}`，其中作者 ID 可空、名称始终为安全显示文本、`body` 为纯文本，B1 的 `images=[]`，时间为 Unix 秒。`limit` 为 1–50；按 `(created_at DESC, comment_id DESC)` 稳定翻页，游标在服务端校验，无效游标为 422。`POST` JSON `{body,client_request_id}` 创建纯文本评论，正文 trim 后 1–4,000 字符；空白、超长、额外 actor 字段为 422。`client_request_id` 必须是小写标准 UUID v4 字符串（36 字符，前端每次新建草稿生成一次）；首次成功 201 返回上述评论 DTO，完全相同的重试 200 返回原 DTO，不重复写事件；相同请求号配不同规范化正文为 409。新增评论不递增待办 `version`，因它不覆盖待办正文或字段。

两个接口及 B2 图片接口每次都从认证用户重新校验**当前**项目成员资格，并确认待办属于该项目且未软删除；未知项目/待办、跨项目 ID、非成员与撤权者统一 404。任意当前成员可评论及读取本项目待办的评论；普通成员不可由评论接口取得待办编辑权。评论提交在事务中按“成员行 → 待办行 → 评论/事件行”锁序检查，成员撤销或待办删除与评论提交可串行化；失败不得留半条评论。新增白名单事件 `project.todo_comment_created`，`object_id=todo_id`、载荷不含正文/图片/路径；成员动态可见，“与我相关”首版仅评论作者本人可见，不把项目留言与待办评论混在一起。

## B2 格式、图片与撤权

给待办增加 `description_format TEXT NOT NULL DEFAULT 'plain' CHECK (description_format IN ('plain','markdown'))`，取值严格为 `plain|markdown`，现有行迁移后为 `plain`。现有 `description` 字段继续保存正文且维持 4,000 字符上限；B2 起**所有**待办列表、详情、创建、PATCH 和批量响应都增加 `description_format`，Dashboard `ProjectTodo` 类型同步增加此必填字段。创建时未传格式默认为 `plain`；PATCH 只改状态/处理人/标题时保持原格式，传 `description` 却不传格式时按 `plain` 写入，同时传 `description` 与 `description_format=markdown` 时保存 Markdown；只传格式而不传正文为 422。PATCH 仍必须带 020 的 `expected_version`，成功后版本递增，409 时保留用户草稿并提供刷新比较。项目专用 Markdown 渲染器不使用 `dangerouslySetInnerHTML`，跳过原始 HTML、禁图片节点，并只允许 HTTP(S) 链接；链接另开时使用 `noopener noreferrer`。现有全局聊天 Markdown 渲染器不得直接用于项目待办。

图片不预先上传到服务器。B2 在**同一个**评论 POST 增加 `multipart/form-data`，字段固定为 `client_request_id`（必填）、`body`（可省略，等同空字符串）和重复的 `images` 文件字段，顺序就是展示顺序；无图片的 JSON 请求仍可用。正文 trim 后为 0–4,000 字符，正文为空时必须至少一张有效图片；最多 5 张，每张最多 8 MiB、单次合计最多 20 MiB，项目评论图片总额最多 512 MiB。服务器按实际文件签名和解码结果验证 PNG/JPEG/WebP，拒绝 SVG、GIF、伪装 MIME 和超限数据；文件名由服务器生成，不使用客户端路径。

B2 新表 `project_todo_comment_images` 固定保存 `image_id` 主键、`comment_id` 外键、服务器生成的 `object_key`、`size_bytes`、`sha256`、`media_type`、从 0 开始且每条评论唯一的 `position`、`created_at`；项目和待办归属从 `comment_id → project_todo_comments.todo_id → project_todos.project_id` 联接推导，不相信请求中的项目/待办 ID。新增 `project_todo_comment_image_usage(project_id PRIMARY KEY,used_bytes)` 在成员/待办锁之后锁定并原子预留项目容量；软删除待办的未清理图片仍计入额度。评论 DTO 的 `images` 始终按 `position ASC` 返回，单项固定为 `{image_id,media_type,size_bytes,position}`，不返回内部路径或内容哈希。JSON 与 multipart 使用相同的评论 DTO、UUID v4 请求号和 201／幂等 200／冲突 409 语义。幂等指纹按规范化正文、图片顺序、每张图片的**实际**媒体类型与 SHA-256 计算；同请求号不同内容 409，不重复计费或留多份图片。

私有根与项目资产分开，复用其安全打开/无符号链接路径约束；存储及 DB 变更采用受控临时文件、失败回收与孤儿清理，故障注入覆盖搬运成功但提交失败。请求体在写入磁盘前按字节限额截断，事务内再次校验成员/待办与容量；两个并发上传不能同时绕过上限。

`GET /api/projects/{project_id}/todos/{todo_id}/comments/{comment_id}/images/{image_id}` 每次校验当前成员及四层归属，返回已验证类型的内联字节，带 `Cache-Control: private, no-store` 与 `X-Content-Type-Options: nosniff`。前端经登录态 fetch 获取并生成本地 Blob URL，在评论卸载、项目切换、撤权或重新加载时撤销；不使用长效签名链接、公共静态目录或外站图片地址。撤权后旧 HTTP 链接必须 404；已在客户端内存中的既有字节不被误称为远程可撤销。待办软删除后图片也不再可读；物理回收另依安全清理策略，绝不依赖 UI 隐藏来保护字节。

## 分工、测试和完成定义

批准的路由保持不变：`claude-bailian/qwen3.8-max` 负责 B1/B2 后端迁移、仓储、事务 ACL 与图片存储；`opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash` 负责不重叠的 Dashboard API/双栏详情/编辑及粘贴交互；`qwen-code-review/glm-5.3` 仅只读审查。Codex 固定合同与文件白名单、整合、补缺、独立复测及向 `xiongbao` 远端推送。当前额度/超时门禁不因设计批准而自动改用其他实施路由，迁移号与工作树基线在派工前重核。

B1 先写失败测试：双数据库新库及旧库升级、owner/member/outsider 和撤权/跨项目/已删 404、成员与待办删除并发、相同/冲突请求号、评论与事件同事务、动态无正文、游标同秒排序；前端验证双视图同 ID 打开、旧纯文本无损、深链/后退/Escape/焦点、409 和错误重试。B2 再测 Markdown XSS 与危险链接、旧客户端格式兼容、伪 MIME/SVG/超限/并发配额、磁盘/DB 故障回收、撤权后的图片旧链接 404、Blob URL 释放及粘贴失败保留草稿。SQLite 与真实 PostgreSQL 分开报结果；1280×768、800×728 的登录态三身份浏览器旅程与 WorkBuddy 实机逐状态截图另行核对。GLM 固定 SHA 只读审查、定向回归、类型/格式/构建和仓库 ship bar 均需各自留证；任一片完成只核销其实际通过的能力。

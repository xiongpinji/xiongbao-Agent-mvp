# PS-04B：真实 PostgreSQL 归档往返补验

日期：2026-09-28。固定源码 `04481724ba519ea16bde5d5f9228cab536a0c24a`。**真实 `create_system_backup → tar.gz → restore_system_backup` 已完成数据库与 B2 私有评论图片切片的往返；三个坏包均在修改目标数据库与图片树前被拒绝。** 这是 [B2 验收](PROJECT_TODO_DETAIL_PS04B_B2_EVIDENCE.md) 中原先缺少的 PostgreSQL tar 归档证据，不是完整 PostgreSQL HTTP／浏览器或整个系统目录的验收。

本轮没有产品代码修改。从 B2 代码 `f1e6ccaf` 到此固定源码，备份模块、评论仓储和评论图片存储没有差异。脚本使用 canonical 源码与已核验的 WSL 虚拟环境：Linux WSL2、Python 3.12.13、psycopg 3.3.4、Pillow 12.3.0、PostgreSQL 客户端及服务器 18.6；未启动 Agent／Gateway／HTTP 服务，也没有调用模型供应商。

## 真实往返与拒绝结果

| 验证项 | 实际结果 | 证据边界 |
| --- | --- | --- |
| 原生 PG 命令 | 1 次 `pg_dump`、5 次仅提取图片 COPY 的 `pg_restore`、1 次带 `--dbname` 的数据库 `pg_restore`，共 7 次均 rc=0、stderr 为空 | observer 原样调用、返回原 subprocess 结果；没有替换 dump／restore 或合成 COPY。 |
| tar 归档 | custom dump 的 `PGDMP` 标记、schema 33、PG manifest、图片 index 和两份真实小图字节均匹配 | 整个实际 tar 管线已执行；其余系统目录明确排除。 |
| 数据库恢复 | 关闭并重开目标 pool 后，九张列明表的所有字段与源状态完全一致，状态摘要相同 | 九表范围见下文；没有逐字段比较整个数据库全部对象。 |
| 私有图片恢复 | PNG／JPEG 共两份，文件哈希和经 `ProjectTodoCommentImageStorage.read_object()` 读取的字节一致；用量等于总图片字节 | 旧目标合成图片已消失；不包含项目资产或 Agent 工作区文件。 |
| 图片字节篡改包 | `SLASH_BAD_ARGS`，`comment image archive bytes differ from index` | 无新增数据库恢复调用；目标九表及图片树不变。 |
| 缺少图片包 | `SLASH_BAD_ARGS`，`comment image archive objects are incomplete` | 同上。 |
| 索引摘要与 dump 不一致包 | `SLASH_BAD_ARGS`，`comment image archive index differs from database` | 同上；索引仍是合法结构，真实 dump 保持不变。 |
| 恢复后仓储授权 | owner／member 各读两张图共 4 次允许；outsider、错误 todo 关联和撤权 member 各两次，共 6 次拒绝 | 只调用真实仓储入口，不是 HTTP 状态码或浏览器验收。 |
| 源状态与关闭 | 源九表和图片树未变；pool 关闭无错误，临时 cluster 停机 0、随后状态 3、端口不再监听 | 根代理另行只读核对停机、端口及归档哈希；没有重跑诊断。 |

九表及恢复时的行数为：`_schema_version` 1、`users` 3、`project_spaces` 1、`project_members` 2、`project_todos` 2、`project_todo_comments` 2、`project_todo_comment_images` 2、`project_todo_comment_image_usage` 1、`project_events` 2。样例包含旧纯文本描述、Markdown 描述、成员文字评论，以及一个带 PNG／JPEG 的纯图片评论；作者、请求指纹、角色、对象定位、图片顺序与摘要等原字段均随九表比较。

source／target 九表摘要均为 `b60061f8a1d6bb71c572068efe46aa6bafaffde950f4db64db3897d960109584`。这一比较发生在恢复后、仓储撤权检查前；随后脚本只在目标合成项目删除 member 关系来验证拒绝，不把最终目标状态宣称为仍与源逐字段相同。

## 目标绑定与生命周期

只新建 `/tmp/xiongbao-ps04b-backup-_ltrcqnm/cluster`，账号 `ps04b_qa`，loopback 端口 `46693`，两个不同的专用库 `ps04b_source_3caefbcdb110` 和 `ps04b_target_91ab8a3af3a9`。每个库初始化前核对不存在 schema watermark；备份／恢复前分别用 pool 和同次调用的 `db_config` 直连核对数据库名、账号、端口及 `SHOW data_directory`，数据目录必须等于该新 cluster。

所有用户、项目、评论和图片均为合成数据，两个 home、正例包和坏包都在同一个新 Temp 根内。源写入在备份前停止，目标有独立的旧数据库及图片样例；正例比较证明旧目标相关行和图片被替换。普通同源恢复显式 `preserve_users=False`，不进入迁移导入的用户保留／剪除分支。

`finally` 关闭 pool、只对本次 cluster 执行必要的 fast stop 并检查状态与端口。Temp 根保留，无递归删除；归档 `valid-backup.tar.gz` 为 20637 字节，SHA-256 `3bf2d59add22e90222f3797fc2c9562010488eb682b589d78f9208f26764151d`。源码 worktree 的 `.git` 使用 Windows 路径，因此脚本显式调用已核验的 Windows `git.exe` 配合 `wslpath` 只读绑定源码，未改写 `.git` 或创建替代源码副本。

## 可复核产物

- [原始执行结果](evidence/ps04b-pg-backup-20260928/pg-backup-result.json)：实际库身份、原生命令结果、九表行数和摘要、图片哈希、三种坏包、仓储 ACL 及停机结果。
- [源码与脚本指纹](evidence/ps04b-pg-backup-20260928/source-bindings.json)：执行前记录的九个脚本／源码文件，其字节数和 SHA-256 已在执行后重新核对。
- [完整诊断脚本](evidence/ps04b-pg-backup-20260928/probe_pg_backup.py)：与实际执行文件逐字节一致，SHA-256 `a8ffa895f4be4f8b623f9040497ff6d236d65b3e3d0e2254b87391d7f16dd27f`。脚本 Ruff 与格式检查通过。

目录内局部 `.gitattributes` 仅对这三份归档关闭 Git 换行转换；JSON 保留常规空白检查并识别原始 CRLF。原始结果和指纹 JSON 均与工作区外执行产物逐字节一致。独立只读代理先核对执行前脚本及目标绑定，执行后又只读核对源码指纹、有效包与两棵图片树、三个坏包的精确差异及停机／端口状态，未发现 P0/P1/P2；没有重跑、启动数据库或清理 Temp。本轮不是 GLM 固定 SHA 补审。

执行入口使用 `/home/canqu/work/xiongbao-030-pg-codex/.venv/bin/python`，参数 `--repo` 为 canonical worktree 的 WSL 路径、`--output` 为 checkout 父目录下的 `output/qa/ps04b-pg-backup-20260928`、`--expected-sha` 为上述固定 SHA、`--git-exe` 为已核验的 `/mnt/c/Program Files/Git/cmd/git.exe`。重现时应先核对源码／依赖指纹，另选全新输出目录；脚本每次另建全新集群，不能连接已有数据库替代。

## 未随本轮核销

`include_config/workspaces/skill_packages/plugins/knowledge/chats` 均为 False、`agent_rows=[]`。因此 config、Agent 工作区、技能、插件、知识文件、现有聊天保留以及有聊天 canary 的排除行为未验；`preserved_chat_rows=0` 只代表本次目标没有这些聊天样例。HTTP／浏览器、Windows 原生 PG 客户端、历史生产库、并发写入中的跨资源一致快照与真实供应商均未验。

现有 PG wrapper 接受 `pg_restore` rc=1，本轮另行要求所有原生返回码恰为 0，未修改该 wrapper，也未演练数据库开始还原后的故障。PostgreSQL 数据库与图片目录不是跨资源原子事务；原异常路径尝试回滚图片树，不代表已部分恢复的数据库自动回滚。三个坏包结果只证明本轮前置校验阻止了进入数据库恢复。

本次提交只含诊断归档与验收台账。Windows 缺少 `make`，证据提交显式使用 `SKIP_PRECOMMIT=1`，没有宣称默认 hook／`make all`／新的全仓回归或前端构建通过，未纳入仍失败且未跟踪的 030 UI RED 测试。文件模式默认开关保持 False；本轮不核销 PS-08 或 WorkBuddy 整体 1:1。

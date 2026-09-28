# PS-04C1 日期、可编辑优先级与标签：冻结交付记录

状态：C1 的各实施片规格/质量、全量本地与真实数据库/浏览器门禁已通过；68 项源/测试字节冻结。本文封存于最终整合和 Git 发布前，最终接受仍由 QA/ship/c1-integrated-review-final/review-result.json 的 C1_INTEGRATION_PASS、精确提交门禁和 QA/ship/c1-publish-result.json 的远端核对共同确认。本文不提前声称提交或推送。

本片为同一组项目待办提供开始日期、截止日期、自定义优先级与标签。新建、既有表格/看板和双栏详情使用真实关系数据；管理者可创建、重命名、改色、排序、停用和恢复目录项。停用保留历史关联，恢复冲突保留原项。成员只能修改符合已有角色规则的待办，目录配置由 owner/admin 管理；撤权与项目删除后拒绝读取旧待办、评论和图片并清除当前页面私密内容。

日期使用服务器时区的日历日，未知服务器日期阻止日期提交；省略与显式 null 分开。旧逾期值可以保留，修改截止日期时按事务内服务器今天验证。待办 version 与目录 revision 分开：冲突保持草稿，必须手动刷新、比较和重新确认，后台读取或评论/状态更新不代替日期与目录比较授权。

034 的 SQLite 原子升级与 PostgreSQL 配对迁移保留原待办 ID/version、Markdown 格式、评论、图片关联及 usage。受控备份的数据库与评论图片安装发生异常时补偿恢复；补偿本身失败时，在通用临时目录之外保留实际数据库与 PNG preimages，提供安全的恢复摘要。

## 冻结执行证据

| 门禁 | 当前可核查结果 | 证明边界 |
| --- | --- | --- |
| BE v4 全量 | Ruff check/format、mypy 551 源文件、4854 passed / 45 skipped，全命令 exit 0 | WSL 的实际 not-live 默认回归，独立私有短 Linux 临时目录；不是付费模型或生产验收 |
| FE v7 全量 | ESLint、Prettier end-of-line auto、tsc、236 文件 / 1686 测试、隔离 Vite build，全命令 exit 0 | Windows 既有依赖；原 5 秒测试时限不变，既有 warnings 保留 |
| PG3 专项 | 20 个真实 PostgreSQL 用例通过；独立池/PID、锁等待、日期/目录/撤权和受控备份 | 20 个独立测试库已移除，集群停机、端口关闭；不是全系统所有并发场景 |
| Browser13 | 真实 PG/TCP API 与浏览器的 21 阶段全部通过；25 次严格几何测量零越界 | 合成 owner/admin/member/outsider、1280×768 与 800×728；未控制用户真实账号或 WorkBuddy 桌面 |
| SPEC → QUALITY | M1/R1/M2、备份补偿和前端基线已分别通过；M3 SPEC → QUALITY v6 通过 | M3 fresh 14 个 React/Antd 场景与 4,890 日期检查通过；最终整合仍待决定，旧探针不写成 fresh execution |

浏览器包括九条目录写路径、C1 与旧 B2 的刷新和同 ID 持久化、日期与目录 409 比较、角色权限失败草稿、三个图片格式的私有字节、账户/项目切换、旧令牌撤权及项目软删除。粘贴输入由 DataTransfer/ClipboardEvent 合成，不能称真实操作系统剪贴板验收；表格/看板是既有入口，C2 的五视图配置和全项目 SQL query 尚未实现。

## 平台等价提交门禁

Windows 没有 make；本片按批准计划执行平台可用的全部等价检查。原 .githooks/pre-commit、Makefile、仓库与全局 Git 配置不改。业务提交不用 SKIP_PRECOMMIT 或 --no-verify；仅本次 git -c core.hooksPath=<仓库外审核目录> commit 使用冻结证据核验 hook。它必须核对真实成功状态、完整命令与清理、68 项实际源/测试字节、6 项只读上下文、三份批准 LF 合同、approval、基线 HEAD 和精确暂存区。

不声称运行了 make all、旧 testmon hook 或默认 LF 全树重写；Prettier auto 保留既有 CRLF。构建输出位于隔离 QA 目录，没有打包发布、修改生成的 Python dashboard 或部署。git write-tree 只为确认候选索引树创建 Git 对象/cache-tree，不改产品文件；“核验只读”不泛指整个 Git 对象库。

## 保留的失败与后续差距

原 SPEC/QUALITY 失败、未完成的回归、真实几何 RED、报告读取/替换竞态及启动超时全部保留。Browser12 对 FullPlan 项目删除后的文案断言不适用于当前上下文；独立复核后只修正 QA 的精确通知，五条 404 和私密内容缺席断言原样，由 Browser13 完整通过。旧基线只以 detailId 控制详情渲染；C1 新增 notFoundError 保护和弹层清理，不能称新旧完整渲染一致。

GLM 尚未补审，外部三条路由的配置保留；本片原生 Codex 与按文件分工子代理由用户单独授权，不冒充外部 CLI job。WorkBuddy 逐状态视觉、用户安装、C2 五视图、附件/子待办/来源导入、完整系统备份与所有 PG 并发矩阵仍有独立差距。完整项目空间“真实旅程 + 视觉”双验收仍为 0/11，030 文件模式仍 False/NO-GO，030 UI 与 045-F 不属于本授权。

下一步必须先完成 C1 的最终整合、精确暂存和非强制 fork main 推送，然后并行启动 Q1 纯定义解析与 Q2 035/派生键/备份；Q3、Q4、Q5 按已批准依赖顺序继续。

完整命令、结果 SHA、68 文件原始 SHA256 与实际 Git clean-filter blob 见 [机器证据索引](PROJECT_TODO_PS04C1_EVIDENCE_20260928.json)。最终报告不把自身 SHA 嵌入被审文档，避免自引用；提交 hook 会固定最终独立报告的实际 SHA。

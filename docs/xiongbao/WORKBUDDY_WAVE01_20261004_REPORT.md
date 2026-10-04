# WorkBuddy 对齐第一波验收记录

本波在 `feature/workbuddy-full-parity-20261004`、基础提交 `187c70a30c4f6ab0edba240157fd22daf07774f7` 上实现 045-F 资产移动/重命名前端和 030UI 私有任务文件抽屉接线。用户已统一授权固定规格的 native 承接和不可用 GLM 的独立替代审查。整体 WorkBuddy 对齐仍在执行，本文不代表完整产品交付。

## 已实现行为

- 资产文件与文件夹行菜单可重命名和移动；重命名弹窗预填名称，Enter 提交、Escape 取消。移动弹窗逐层显示文件夹、分页加载、选择项目根目录，以 `parent_id: null` 表达根目录；目录自身不出现在目标候选中。
- 同名冲突保留草稿和目标位置；请求期间防重复提交；项目切换、关闭和晚到响应受到生命周期约束。资产节点及版本身份保留，回收站继续显示删除时冻结的位置。
- 私有任务显式传递 `privateTask`，工作区抽屉只接受匹配任务 ID 的内部 Agent；普通 Agent 保持既有路径。此 UI 接线没有激活尚未通过门禁的文件模式，`PROJECT_TASK_FILES_MODE_ENABLED` 仍为 False。

## 当前字节的独立审查与普通质量检查

045-F 的五文件候选指纹为 `4a5e251c933e83a6d43b8eb23beca24cc29d637aeec3d45ea69d5ed1a73d371e`，独立只读审查 GO、无发现；相关资产、PDF、版本组件测试 110 项通过。030UI 四文件候选指纹为 `a4b641ff6bb90ffcd744216d7ef46c0bab66641522f54b0eddf3e5eecd73b03a`，独立只读审查 GO、无发现；Root 重跑 23 项相关测试通过。候选指纹是文件清单摘要，不是 Git 提交 SHA。

Windows Python 3.13.13 本地普通质量检查均退出 0：

| 命令 | 实际结果 |
| --- | --- |
| `make all PYTEST_JOBS=4` | Ruff 检查/格式和 mypy 通过；5329 passed、319 skipped、25 warnings |
| `cd dashboard && npx tsc -b` | 通过 |
| `npm run test -- --maxWorkers=2` | 251 文件、2008 测试全部通过 |
| `npm run lint` | 通过；不宣称无警告 |
| `npm run format:check` | 通过 |
| `npm run build` | 通过，生成当前 dashboard 构建产物 |

采用仓库本地官方 MSYS2 make 和既有 Git for Windows 运行库，在子进程范围设置 PATH；未改仓库 Makefile、hook 或质量门禁。供应商环境变量只在测试子进程置空，没有修改用户凭据。完整日志与命令/时间/摘要回执保留在私有 `_local/qa/full-parity-20261004/normal-gates-v1/`。提交仍须执行原 `.githooks/pre-commit`，其实际内容为 `make precommit` 和 dashboard build。

## 实际浏览器证据与边界

使用独立合成账号、项目、SQLite 和真实认证/API/资产服务/存储进行浏览器旅程，未替换资产权限或存储。实际观察到：同名移动冲突后保留目标与原文件；PDF Enter 改名；当前 PDF 第二版正常渲染且仍有两个版本；文件移动到目标 A 后在该目录可见；同名重命名冲突保留输入，Escape 取消；显式选择项目根目录移回；文件夹目标选择器排除自身；整个文件夹移动到目标 B 并改名后，其子文件仍可见。两次桌面视口为 1280×768 和 1273×634。

停止记录确认节点/版本 ID、对象 SHA-256 和当前版本指针未变；数据库池、监听器、任务 watcher、测试 HTTP client 均已关闭，清理错误为空。原始测试运行总结果保留为 **FAILED_OR_INCONCLUSIVE / exit 2**：专家市场入口尝试查询 `api.skillhub.cn`，被隔离 DNS 门禁拒绝。来源为专家页面的 `list_expert_hub → browse_skillsets`，未实际建立外部连接；不能把这次总运行改写为通过或宣称零外部尝试。资产旅程及数据不变性与该总运行结论分别记录。

当前浏览器只覆盖合成 owner/admin，不代表 member/撤权成员实机验收；第 101 个文件夹分页由组件测试覆盖，尚未进行浏览器分页验收。真实 PostgreSQL 并发、供应商调用和本轮 WorkBuddy 原生窗口同状态视觉未验证。现有文本文件仍提示不支持在线预览；Windows 交互终端报不支持，均保留为后续差距。

## 下一波的技术门禁

PS04D D1 Windows/POSIX 正常存储读写探针通过，但严格目录句柄绑定及授权持有期间的字节读入尚未证明。Windows 正向句柄探针中相对发布返回 WinError 87，原始失败和所有句柄关闭证据保留，继续诊断；D1 仍 NO-GO。

046 M0 合成正向接缝 16 项通过，但完整 current actor/resume、普通 memory/workspace/connector 隔离、授权存储解析及首轮唯一 claim/未知状态仍缺合同与实际证据，M0 为 NO-GO。不会提前开放资产进入任务或减少普通工具能力。W09 历史持久化失败回滚的最小任务包可独立准备；全量依赖与所有权见 [全量计划](WORKBUDDY_FULL_DELIVERY_20261004.md)。

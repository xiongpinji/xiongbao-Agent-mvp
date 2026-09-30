# Windows 桌面基线验收（021，2026-10-01）

固定源码 `7444a9204af655e90bce72f976f1f1babe59e0d3` 上，Codex 实际运行 Windows Go 测试与开发壳编译：**34 个测试通过、0 失败、0 跳过，开发 PE 编译退出 0**。本批未修改桌面业务源码，73 个已跟踪桌面文件的前后哈希一致。它建立第 021 项的构建基线，不代表熊宝原生品牌、可安装产品或 WorkBuddy 1:1 已验收。

## 验证方法与结果

宿主未发现 Go、Wails CLI、Task 或 NSIS。Go 1.27.1 Windows/amd64 ZIP 放在仓库外 owned QA 目录；下载前核对 [Go 官方发布元数据](https://go.dev/dl/?mode=json)，下载后校验长度与 SHA256，再校验解压目标位于该目录。没有运行系统安装器或修改全局 PATH。Go 模块按现有 go.mod/go.sum 获取，`GOFLAGS=-mod=readonly`；缓存、临时文件与 `OCTOP_HOME` 均指向本批 QA 目录。

| 检查                                                            | 实际结果                                             | 证据边界                                                                            |
| --------------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `go test -count=1 -json ./...`                                  | 退出 0；34/34 测试通过                               | `octop.desktop` 包通过；`cmd/devserver` 无测试文件，包级 skip，与测试级 0 skip 分列 |
| `go build -trimpath -buildvcs=false -o <owned-QA-output.exe> .` | 退出 0；18,274,304 字节 Windows/amd64 PE             | 仅开发壳；没有执行该 EXE，没有 production 内嵌 portable，也不是 NSIS 安装包         |
| 73 个已跟踪桌面来源文件                                         | 测试前后字节一致                                     | go.mod/go.sum、旧 Logo、窗口文案和打包配置均未修改                                  |
| owned Windows Jobs                                              | 两阶段都自然结束；active=0、terminated=0，Job 已关闭 | 测试 Job 总进程 643，编译 Job 总进程 355；只管理本批创建的进程树                    |

现有测试覆盖桌面语言默认值、窗口恢复与标题栏、设置迁移、健康检查，以及使用临时目录和假 portable 的备份/升级失败保持旧运行时等路径。它们不是实际用户安装、升级或卸载验收。原始 QA 日志仍保存在本地仓库外；[可核对摘要](evidence/desktop-windows-baseline-021-20261001.summary.json) 包含测试名称、文件哈希、命令退出码与资源结束状态。

Go ZIP SHA256：`a3911b5e0e1b1053f25ed0675f4c1c6aad1e2bfcf253df2b9be4caabd2edd95d`；开发 PE SHA256：`7c1a564e2f21630a53b12468d688cb2fb62b52ad4edba02e633986267c553285`。编译输出没有提交到 Git、没有发布生产下载。

## 第 021 项仍需完成

当前 Windows 原生标题、托盘提示、启动/设置文案和部分 Logo 仍是 Octop。按照[已批准总计划](MASTER_PARITY_PLAN.md)，桌面显示品牌切片已编成八文件任务，继续交原 Claude/Qwen 与 OpenCode/DeepSeek 实现路由，GLM 仅只读。Claude/Qwen 本批在改动前被月额度 429 拒绝；不能据此把实现标成完成，也不能将以前其他任务的原生接手授权扩大到本片。

后续需要分别核验：显示品牌源码与内嵌资源、固定候选只读审查、熊宝 production portable 与安装器名称/图标、实际安装/升级/卸载和通知、真实窗口及 WorkBuddy 同状态视觉。保留 `OCTOP_HOME`、历史 `.octop` 数据与兼容身份。没有运行桌面应用、安装器、真实模型、用户账号或生产服务。

本次没有重新运行 Python/前端全量测试，也没有宣称默认 `make all` 通过：Windows 当前没有 make，且没有相关业务源码改动。发布只验证本批文档、摘要固定字节、链接、格式及精确暂存范围。GLM 新审查未运行；[25 项对标台账](WORKBUDDY_PARITY.md) 和 021/024 均保持未完成。

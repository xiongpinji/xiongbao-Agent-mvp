# PS-05B 024/025：旧库逐版升级补验

日期：2026-09-30。固定业务源码 `deb94e8373be00d4a8793a9196a2fa5ff634fc2e`。本批没有修改 024/025 业务实现；在自建、独立、已销毁的 PostgreSQL 18.6 测试集群中，以合成数据验证 `23 → 24 → 25 → 当前 35` 升级链，重点检查既有卡片分享不会自动获得对话正文读取权。

测试脚本先把迁移发现范围限定到版本 23，创建两名合成用户、Agent、Dashboard 线程、项目、成员和任务关联；升级到 24 后写入一条有效的旧式卡片分享；升级到 25 后，直接查询正文授权表为空，接收者读取正文被拒绝。随后升级到当前 35 并重复运行迁移，验证旧卡片仍可见，但 `can_read_text=false`、正文读取仍被拒绝。只有任务本人调用单独的正文授予仓储操作后，摘要才变为 `can_read_text=true`，正文读取进入无投影数据的 `pending` 状态。结果为 **PASS**。

脚本只接受本次测试集群的收据路径，不接受应用 DSN；执行前核对回环地址、端口、数据库用户和 `data_directory`，只在该集群中创建唯一命名数据库。测试后连接数为 0，测试库已删除；集群正常停机，`pg_ctl status=3` 且端口关闭。清理脚本再次校验收据、停机状态与 `/tmp` 下唯一自建目录后，仅删除该集群目录，并验证其不存在。

[升级脚本原文](evidence/ps05-pg-upgrade-20260930/upgrade_probe.py.txt)、[升级结果](evidence/ps05-pg-upgrade-20260930/upgrade-result.json)、[集群启停收据](evidence/ps05-pg-upgrade-20260930/cluster-result.json)、[清理脚本原文](evidence/ps05-pg-upgrade-20260930/upgrade_cleanup.py.txt)和[清理收据](evidence/ps05-pg-upgrade-20260930/upgrade-cleanup.json)保存于仓库；两个脚本以 `.txt` 保存原始执行字节，避免把一次性 QA 脚本当作产品代码检查。发布的升级结果 JSON 只把换行统一为 LF，内容未变。[SHA-256 清单](evidence/ps05-pg-upgrade-20260930/evidence_manifest.json)绑定五份**发布字节**。首次执行在测试脚本读取 psycopg 行切片时失败，随后仅修正脚本为索引读取并在新建测试库上完整重跑通过；该首次失败不计为通过证据。

这是**合成 v23 数据的数据库升级补验**，并非真实用户旧库的迁移、HTTP/浏览器全流程或生产升级。既有[全新库 PostgreSQL/TCP 与 Chrome 补验](PROJECT_TASK_SHARE_PS05_PG_TCP_ACCEPTANCE_20260930.md)覆盖了另外一组局部路径；两者合并仍不能证明归档与授权同发、所有锁序、成员移除与授予同发、任务级全入口 ACL、协同写入和 WorkBuddy 1:1 行为。PS-05B 与总体 25 项仍未核销。

# 030A 文件任务：最新主线 Windows 路径补验

验证日期：2026-09-27。固定源码：`585c0fcc9fb30aa375298979631735dd24ac4ff3`（验证开始时的 `xiongbao/main`）。在独立、干净的 Windows Git worktree 中，用 Windows Python 3.13.13、pytest 9.1.1 运行；测试临时目录位于 `C:` 的 NTFS 卷。未改代码、账号或真实项目文件。

**源码绑定门禁：**借用的 Windows 虚拟环境原本以 editable 模式指向另一个旧检出。首次未设置 `PYTHONPATH` 的运行虽也显示相同通过数，但实际 `import octop` 指向旧检出，**不计入本记录的证据**。重跑前设置 `PYTHONPATH=<本隔离工作树的 src 绝对路径>`，并以 `python -c "import octop; print(octop.__file__)"` 确认它解析到本工作树的 `src/octop/__init__.py`。下表全部是修正绑定后的重跑结果。

| 实测范围 | 命令摘要 | 结果 |
| --- | --- | --- |
| 任务文件路径测试全文件 | `python -m pytest -q tests/unit/backend/test_project_task_file_paths.py` | **48 通过、13 跳过，退出码 0** |
| 真实 junction 四例单独核对 | 同一文件加 `-q -vv -k junction` | **4 通过、57 未选中，退出码 0** |
| 运行中根/祖先改名三例 | 单独运行 `test_real_harness_running_root_replacement_is_refused`、`test_real_harness_managed_ancestor_replacement_is_refused`、`test_verify_false_after_root_or_ancestor_replacement` | **3 跳过，退出码 0**；另有 4 条依赖的弃用警告 |

四个真实 Windows junction 用例分别验证：路径解析器拒绝越界 junction、文件系统后端拒绝读取越界 junction、根目录本身为 junction 时拒绝、目录列表在子树内遇到 junction 时拒绝。它们是在稳定植入条件下的 check-time 证据；单独的 `-k junction` 输出逐项均为 `PASSED`，而非仅凭全文件绿色推断。

三个运行中替换用例均在尝试重命名**仍被进程使用**的根或祖先时收到 WinError 5（拒绝访问），按既有测试的显式 `pytest.skip` 分支结束，未执行其后的替换—拒绝断言。因此这些用例仍是**未验证**，不能把 `3 skipped` 写成路径门禁通过，也不能由稳定 junction 用例推断不存在 check→use/TOCTOU 竞态。其他 13 项跳过不在本记录中逐项核销。

本次补验更新了最新主线的原生 Windows 证据，但没有改变[030A B+ 的激活 NO-GO](PROJECT_TASK_WORKSPACE_030_A_BPLUS_ACCEPTANCE.md)。`PROJECT_TASK_FILES_MODE_ENABLED = False` 保持关闭。真实已认证浏览器创建—读写—重启—撤权、实际模型工具回合、完整重解析点矩阵、运行中目录替换以及同账号 TOCTOU 处置仍须分别验收；030A 和 WorkBuddy 项目任务空间均未整体核销。

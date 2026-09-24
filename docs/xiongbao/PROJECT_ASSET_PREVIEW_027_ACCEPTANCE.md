# 027 · 项目资产 PDF 历史版本预览验收记录

状态：本地集成、定向检查和三身份浏览器旅程通过；固定代码提交的 GLM-5.3 只读复审、远端推送与托管 CI 待完成。本记录只覆盖 PS-06B-1P，不核销项目空间或 WorkBuddy 1:1。

## 行为边界

版本弹窗右栏仅对文件名为 `.pdf`、所选版本 MIME 为 `application/pdf` 且元数据大小在 `(0, 25 MiB]` 的版本启动预览。字节每次经过已有的成员授权历史下载接口取得；返回后再次检查实际大小及 `%PDF-` 签名，交给本地 PDF.js 渲染，不生成公开 URL，也不调用第三方预览服务。其他类型仍显示占位和独立下载按钮。切换版本或关闭弹窗会取消旧请求；授权下载的 404 走父页原有的清空弹窗和重查权限路径。回收孤儿文件时的逐条目异常日志不再附带含私有绝对路径的异常文本。

## 独立验证

| 验证 | 结果与边界 |
| --- | --- |
| Windows 资产单元 | `tests/unit/db/test_project_assets.py` 84 通过、5 项 POSIX 专项跳过；新注入测试先在旧代码 RED，再在候选代码通过。WSL 后端候选同文件 89 通过。 |
| Windows / WSL API 集成 | `tests/integration/test_project_assets_api.py` 两侧各 25 通过，覆盖现有项目成员与版本下载链路。 |
| 前端定向 | 项目资产 API、PDF 子预览、版本弹窗及相邻项目页共 6 文件、83 用例通过；`tsc --build --force` 通过，避免增量缓存掩盖两条实施线之间的签名错误。 |
| 静态与构建 | Ruff 全仓 check/format 通过（1090 文件），mypy 535 个源文件通过；前端改动文件 Prettier、全仓 ESLint（0 error、67 条既有 warning）、生产构建通过。Vite 大包和混合导入警告仍在。 |
| Windows 全仓非 live | `pytest -q -n 8 -m "not live"`：3985 通过、123 跳过、17 条警告，退出码 0；其中一条既有 `test_browser_api` 子线程在 Windows 控制台发生 UTF-8 解码 warning，未使测试失败。此为分别运行的 `make all` 等价检查，不宣称运行了 `make all` 本身。 |
| 隔离服务 + 无头 Chrome | 本地假 provider 环境的 owner/member/outsider 旅程两次通过：上传两版真实 PDF、旧版按 `version_id` 预览和切换、非成员 404、撤权后旧成员切版触发弹窗关闭；请求均留在本地。第二次还验证 760×720 窄屏下载按钮可滚动到可操作区域。此证据不是正式安装包或真实 provider 验收。 |

本地截图：`outputs/xiongbao-project-asset-preview-027-wide.png`（1280×768）和 `outputs/xiongbao-project-asset-preview-027-narrow.png`（760×720）。截图确认 PDF 内容进入双栏右侧；窄屏操作需向下滚动。它们不是 WorkBuddy 逐像素对齐证明。

## WorkBuddy 差距与未验

WorkBuddy 5.6.2 既有项目的资产弹窗实机观察显示双栏版本/预览联动；观察文件仅一版，不能据此确认其多版恢复细节。熊宝已补本地 PDF 历史版预览，但 Office、图片、文本预览、预览工具栏与视觉细节、删除/回收、项目配额、移动/重命名、添加到项目任务、项目配置以及完整项目协同仍未达。真实 PostgreSQL、桌面安装包、正式用户登录/授权、每一种 WorkBuddy 页面状态也未验。

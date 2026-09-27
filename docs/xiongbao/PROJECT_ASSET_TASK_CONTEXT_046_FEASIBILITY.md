# 046 项目资产进入任务：现有代码接线与约束

状态：2026-09-28 的**只读可行性盘点**，代码基线 `b6451d15278922ed479cf02db2bc320ef647cc0f`。这不是已批准的产品规格或已实现能力。WorkBuddy 5.6.2 的可见交互见[实机观察](PROJECT_ASSET_TASK_CONTEXT_046_OBSERVATION.md)；其任务提交后内部存储、版本和模型读取规则没有被观察到。

## 当前用户链路

- `dashboard/src/pages/Projects/ProjectDetail.tsx` 的 `taskComposer` 输入与发送按钮仍禁用，在四个项目页签下共用。`ProjectAssets.tsx` 的“添加到任务”总按钮也禁用；文件、文件夹行没有可用的项目任务引用回调。
- 当前能用的路径是 `ProjectTasks.tsx` 的弹窗先创建本人私密项目任务，再跳转到 `/chat/{agent}/{thread}`；创建动作不自动发送首轮。前端 `projectTasksApi.create` 和后端 `CreateTaskBody` 只收 Agent、指令摘要及有限的专家/模式字段，没有资产节点或版本引用。`migrations/026_project_task_context.sql` 也没有任务资产关联。
- 普通聊天首轮由 `ChatInput.tsx`、`useChatSend.ts`、`chatStore.ts` 经 WebSocket `user_turn` 进入 `src/octop/api/routers/chat/turn.py`。前端 `ChatAttachment` 是 Agent 工作区上传的 URL、文件名、类型和 `workspacePath`，没有项目资产 ID。

## 资产与运行时边界

- 项目资产位于 `src/octop/infra/projects/asset_storage.py` 管理的独立私有根。`ProjectAssetService.prepare_download` / `prepare_version_download` 及仓储查询按项目成员、活动节点和版本归属做检查；安全 DTO 不下发物理对象路径。文件当前版与指定历史版有不同下载入口。
- 普通聊天上传会把字节写入 Agent 工作区 `inbound/`。聊天服务端只接收 `workspace_path`，忽略客户端预览 URL；将项目资产下载 URL、节点 ID 或私有对象路径伪装为这个字段不能形成安全的引用。共享专家 Agent 工作区的访问控制也不等于项目成员撤权控制。
- 普通聊天允许上传多种文件，默认单文件上限为 100 MiB，但运行时对**非图片**一般只把文件名、MIME 和工作区路径写成模型提示，不会自动解析 PDF/Office 内容。图片最多四张尝试转为视觉块；是否被具体 provider 接受仍须实测。`project_task_files` 内部运行时显式拒绝附件块，不能借其绕过该边界。
- 知识库的 `parse_document` 可解析文本、CSV、PDF 和常见 Office 格式，但聊天链路没有调用它。解析器按 `Path` 后缀分派，而项目资产的物理对象名是无扩展名的版本 ULID；复用时需有独立的格式选择与授权读取适配，不能直接传物理路径。
- 项目任务的共享卡片仅有安全摘要字段；对话正文有独立的文本授权。把资产字节、对象路径、可重放下载链接或提取的内容写进卡片或正文，会改变现有的私密边界。

## 尚待产品规格固定

版本固定还是跟随最新版、文件夹在提交时包含哪些文件、重复引用卡片的语义、支持解析的文件类型与大小、任务创建/首轮失败的重试方式、删除或撤权后的再次读取，以及已交给模型的内容不可追回等，均须在正式规格中明确。前端卡片出现、任务建立、模型实际读到内容是三个不同验收层级。

# Human Review Needed · 熊宝-Agent 任务对话效果图

以下核对是视觉/产品确认，不是要求用户此刻暂停实施。静态图可确定可见的桌面布局，却不能证明项目空间的功能和窄屏行为。

## 摘要

| 指标 | 数量与说明 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- |
| 待复核项 | 7 项，按下表逐项关闭 | inferred-implementation | 0.95 high | recommended |
| 低置信度 token | 0 项，`tokens.json` 中无 confidence < 0.6 | screenshot-estimated | 1.00 high | required |
| 低置信度布局判断 | 1 项：窄屏三栏规则 0.54 | screenshot-inferred | 0.54 low | flexible |
| 中置信度 token | 3 项：`typography.size.h2`、`typography.weight.heading`、`spacing.32` | screenshot-estimated | 0.69 medium | recommended |
| 需脚本验证 | 2 类：像素/对比度与浏览器视觉差分 | inferred-implementation | 0.90 high | recommended |
| 资产策略复核 | 1 类：原始角色素材与横幅匹配 | screenshot-inferred | 0.68 medium | recommended |

## 审查清单

| # | 阶段 | 项目 | 当前判断 | Confidence | 验证方式 | Source | Strictness |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 布局 | 窄屏三栏处理 | 未由 1633 px 图证明折叠、隐藏或单栏；不得当既定断点 | 0.54 low | WorkBuddy 实机与熊宝实机逐宽度对照 | screenshot-inferred | flexible |
| 2 | 资产 | 头像/主视觉的原图与裁切 | 图中必须是复杂 raster，具体素材映射未知 | 0.64 medium | 比对用户提供的 logo、角色设定与透明区域 | screenshot-inferred | recommended |
| 3 | 资产 | 右下品牌横幅 | 独立合成素材尚未找到 | 0.68 medium | 核查可用素材；必要时设计合成稿并目视验收 | screenshot-inferred | recommended |
| 4 | 字体 | 中文字体、标题字号字重 | `h2` 与 heading weight 的采样值均约 0.69 | 0.69 medium | 同尺寸浏览器对照字宽、换行与层级 | screenshot-estimated | recommended |
| 5 | 间距 | 左栏 32 px 语义间距 | `spacing.32` confidence 0.68，不能证明是原设计 token | 0.68 medium | 浏览器截屏量品牌/导航/列表垂直节奏 | screenshot-estimated | recommended |
| 6 | 色彩 | 灰字、边框、金色渐变 | 平坦区有采样，尚无对比度和渲染差分 | 0.83 high | 脚本测色/对比度，人工看渐变与焦点态 | screenshot-estimated | recommended |
| 7 | 效果验收 | 任务对话与项目空间分别验收 | 本图仅覆盖任务对话；项目四页签来自 WorkBuddy 实机 | 0.96 high | 同视窗截图差分和四页签真实交互流程 | reference-visible | required |

## 详细说明

### 窄屏规则（必须保留开放）

- **来源阶段**：布局规格；source: screenshot-inferred；confidence: 0.54 low；strictness: flexible。
- **当前判断**：桌面三栏可见；窄屏下栏位次序、折叠阈值与输入框定位未知。
- **不确定原因**：只有一个 1633 × 963 截图，无法从静态画面观察窗口变化。
- **关闭证据**：至少两种更窄的 WorkBuddy 实机视窗与熊宝实现截图；记录焦点、滚动和按钮可达性。

### 品牌资产和项目空间

- **来源阶段**：素材策略与可见元素清单；source: screenshot-inferred；confidence: 0.64–0.68 medium；strictness: recommended。
- **当前判断**：复杂角色必须用授权图像，右栏横幅可能要二次合成；项目四页签不能由效果图推断。
- **关闭证据**：文件级素材映射、实际渲染截图，以及独立项目空间的 WorkBuddy 实机状态记录。

# Visual Analysis · 熊宝-Agent 桌面效果图

## 1. 全局构图

- **UI Type**: `application-dashboard`；source: `reference-visible`；confidence: 0.96（high）；strictness: required。
- **页面尺寸**: 1633 × 963 px；source: `screenshot-estimated`（Pillow 实测文件尺寸）；confidence: 1.00（high）；strictness: required，仅用于该基准视窗。
- **主视觉模型**: structured application layout，左导航／中央任务对话／右推荐栏；source: `reference-visible`；confidence: 0.96（high）；strictness: required。
- **判断依据**: 三块完整、独立的竖向工作区域和可辨的导航、消息、输入、卡片控件。熊宝立绘只占右栏的一部分，不能把整页归为 `image-led-landing`；该候选 confidence 0.08。

以下 bbox 均为此张效果图中的近似可见边界，格式 `x,y,w,h`，单位 px；坐标可经后续像素/浏览器截图校正。

### Visible Element Inventory

| 元素 | bbox | 视觉证据 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- |
| 窗口顶部控制带 | 0,0,1633,46 | 左上三圆点、右上最小化/最大化/关闭 | reference-visible | 0.91 high | recommended |
| 左导航栏 | 0,45,303,918 | 与中央区域分隔的深灰竖栏 | reference-visible | 0.95 high | required |
| 品牌头像与名称 | 20,51,242,68 | 熊宝头像、熊宝 Agent 与副标题 | reference-visible | 0.95 high | required |
| 金色“新建任务”按钮 | 9,143,287,43 | 占左栏宽度的金色渐变横条 | reference-visible | 0.96 high | required |
| 主导航组 | 22,198,200,334 | 对话、智能体、项目、知识库、工具箱、团队协作、任务日历、设置图标与标签 | reference-visible | 0.94 high | recommended |
| 最近对话列表 | 11,561,284,298 | 标题、选中项、更多入口 | reference-visible | 0.90 high | recommended |
| 左下账户区 | 24,884,261,69 | 用户头像、名称、登录状态及图标 | reference-visible | 0.91 high | recommended |
| 中央任务面板 | 319,47,889,903 | 圆角边框围住标题、对话与输入 | reference-visible | 0.96 high | required |
| 中央任务标题栏 | 320,48,886,66 | 左侧标题、右侧操作图标、下边线 | reference-visible | 0.94 high | required |
| 人类消息行 | 350,128,790,82 | 头像、身份/时间、短消息 | reference-visible | 0.88 high | recommended |
| Agent 回复卡 | 414,263,760,491 | 浅一层深灰圆角卡、较长正文、文件卡与任务完成条 | reference-visible | 0.95 high | required |
| 文件附件卡 | 433,577,302,62 | 文档图标、文件名、体积/预览及复制图标 | reference-visible | 0.91 high | recommended |
| 任务完成状态条 | 433,655,723,41 | 绿色完成标识和折叠箭头 | reference-visible | 0.92 high | recommended |
| 中央输入框 | 336,779,852,131 | 大圆角输入区、提示词、底部多个操作胶囊和发送圆钮 | reference-visible | 0.95 high | required |
| 右侧建议栏 | 1219,47,406,903 | 独立圆角容器，与中央隔约 11 px | reference-visible | 0.95 high | required |
| 搜索与通知 | 1233,59,381,39 | 搜索框和铃铛图标 | reference-visible | 0.93 high | recommended |
| 熊宝主视觉 | 1235,112,385,200 | 大型熊宝人物与金色光效 | reference-visible | 0.92 high | required |
| 欢迎与快捷卡 | 1231,311,383,205 | 欢迎语、副标题、三个操作块 | reference-visible | 0.94 high | recommended |
| 推荐智能体卡 | 1231,528,383,184 | 标题和两列专家卡 | reference-visible | 0.94 high | recommended |
| 底部品牌横幅 | 1231,733,383,111 | 熊宝人物、品牌名、圆形箭头 | reference-visible | 0.94 high | recommended |

### Rejected Assumptions

| 假设 | 拒绝原因 | 影响 |
| --- | --- | --- |
| 项目详情四页签 | 此图显示任务对话，不含“动态/计划/任务/资产” | 项目页需另用 WorkBuddy 实机观察；不得从此图推断 bbox |
| 可操作终端或文件浏览器 | 本图只见“文件上传”和附件卡，没有终端/目录树 | 不能仅凭效果图宣称有 shell 或项目文件执行 |
| 右栏是对话详情或真实工作台 | 可见的是搜索、熊宝主视觉与推荐卡 | 不得擅自填入任务审计、协作者列表或图表 |
| 顶部品牌插画可纯 CSS 重绘 | 有复杂角色立体质感、光影、服装细节 | 必须引用用户提供的授权熊宝素材；不要把插画拆成 DOM |

### Asset Strategy

| 视觉层 | 实现方式 | 原因 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- |
| 品牌头像与熊宝主视觉 | 原始 raster 图像，按容器裁切 | 立体角色与金属光效不是 CSS 可忠实重建 | reference-visible | 0.95 high | required |
| 右侧品牌横幅 | 用户提供的独立素材；若无独立横幅，需重新设计素材 | 截图里的构图与文字嵌入图像 | screenshot-inferred | 0.68 medium | recommended |
| 三栏、卡片、输入、图标、文字 | HTML/CSS/SVG | 边界和内容清晰、需要真实交互与响应式 | reference-visible | 0.93 high | required |
| 品牌主视觉的具体裁切源 | 待比对已授权 logo/角色素材 | 效果图不能证明素材原图路径与透明边界 | screenshot-inferred | 0.64 medium | recommended |

## 2. 颜色系统

下表为 Pillow 对本地参考文件指定平坦区域取样；复杂渐变仅抽取可复用端点。精确浏览器呈色和对比度另验。

| Token | Hex | 角色/出现位置 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- |
| `color.bg.app` | `#15191F` | 左栏和右栏深色基底 | screenshot-estimated | 0.91 high | required |
| `color.bg.center` | `#191D24` | 中央任务面板上方/空白 | screenshot-estimated | 0.90 high | recommended |
| `color.surface.card` | `#1C2128` | 回复卡主体 | screenshot-estimated | 0.91 high | recommended |
| `color.surface.composer` | `#20252D` | 输入区 | screenshot-estimated | 0.88 high | recommended |
| `color.surface.raised` | `#2E343F` | 搜索、卡内按钮或升高层 | screenshot-estimated | 0.80 high | recommended |
| `color.border.subtle` | `#343940` | 面板与卡片细边 | screenshot-estimated | 0.70 medium | recommended |
| `color.text.primary` | `#F4F4F5` | 标题及主要信息 | screenshot-estimated | 0.76 medium | recommended |
| `color.text.muted` | `#9EA4AF` | 时间、副标题、输入提示 | screenshot-estimated | 0.72 medium | recommended |
| `color.accent.gold.light` | `#EBC796` | 新建任务按钮左侧取样 | screenshot-estimated | 0.88 high | required |
| `color.accent.gold.dark` | `#7E5D3C` | 新建任务按钮右侧取样 | screenshot-estimated | 0.87 high | required |
| `color.status.success` | `#3ACE8B` | 任务完成标识取样 | screenshot-estimated | 0.83 high | recommended |

### 估算对比度

| 前景/背景 | 估算 | 备注 |
| --- | --- | --- |
| 主文字 / 深色中心 | 高于 10:1（未实算） | `estimated-contrast`，需脚本核验 |
| 次级文字 / 回复卡 | 约 5:1（未实算） | `estimated-contrast`，需脚本核验 |
| 金色按钮深色文字 / 渐变最暗端 | 端点可能低于浅色端 | `estimated-contrast`，需按左右端分别实算 |

## 3. 字体排印

中文图中文字边缘不能从静态图可靠识别具体字体；建议以系统中文无衬线为实现候选，不能把估计写成原字体事实。

| 角色 | 候选字体 | 字号/字重/行高 | 颜色 Token | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- | --- |
| h1/任务标题 | 系统中文无衬线 | 22px / 650 / 30px | `color.text.primary` | screenshot-estimated | 0.73 medium | recommended |
| h2/欢迎卡标题 | 系统中文无衬线 | 19px / 650 / 27px | `color.text.primary` | screenshot-estimated | 0.69 medium | recommended |
| h3/推荐卡标题 | 系统中文无衬线 | 16px / 600 / 24px | `color.text.primary` | screenshot-estimated | 0.73 medium | recommended |
| body/消息与按钮 | 系统中文无衬线 | 16px / 400 / 25px | `color.text.primary` | screenshot-estimated | 0.74 medium | recommended |
| caption/时间与辅助 | 系统中文无衬线 | 13px / 400 / 19px | `color.text.muted` | screenshot-estimated | 0.72 medium | recommended |

## 4. 间距系统

| 值 | 用途 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- |
| 8px | 小图标/文字与胶囊内部紧邻间距 | screenshot-estimated | 0.73 medium | recommended |
| 12px | 中央/右栏间隔、卡片与卡片之间 | screenshot-estimated | 0.76 medium | recommended |
| 16px | 卡片内边距与右栏容器边距 | screenshot-estimated | 0.79 medium | recommended |
| 24px | 回复卡宽松正文与主块间距 | screenshot-estimated | 0.70 medium | flexible |
| 32px | 左导航主入口横向起点附近 | screenshot-estimated | 0.68 medium | flexible |

该图是 1633px 宽桌面状态；没有移动或窄屏样本，断点、最小宽度与滚动条行为只能作为实现推断另行验收。

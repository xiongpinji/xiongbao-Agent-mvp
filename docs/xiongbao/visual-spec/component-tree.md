# Component Tree · 熊宝-Agent 桌面任务对话态

**uiType:** `application-dashboard`（source: `reference-visible`; confidence: 0.96 high; strictness: required）。树形结构只描述参考图 #3 的可见任务对话态。以下 bbox 使用 `x,y,w,h`、单位 px；结构容器可以作为实现推断，但不得借此推断项目空间四页签内容。

```text
AppShell [inferred-implementation]
├── TopChrome [reference-visible]
├── LeftSidebar [reference-visible]
│   ├── BrandIdentity [reference-visible]
│   ├── NewTaskButton [reference-visible]
│   ├── NavItems [reference-visible]
│   ├── RecentList [reference-visible]
│   └── AccountDock [reference-visible]
├── MainPanel [reference-visible]
│   ├── TaskTitle [reference-visible]
│   ├── ChatTimeline [screenshot-inferred]
│   │   ├── MetadataText [reference-visible]
│   │   └── MessageCard [reference-visible]
│   │       ├── BodyText [reference-visible]
│   │       ├── AttachmentCard [reference-visible]
│   │       └── SuccessStrip [reference-visible]
│   └── Composer [reference-visible]
└── RightRail [reference-visible]
    ├── Search [reference-visible]
    ├── HeroImage [reference-visible]
    ├── WelcomeCard [reference-visible]
    ├── RecommendedAgents [reference-visible]
    └── BrandBanner [reference-visible]
```

`AppShell` 仅定义三栏 grid 与顶部控制覆盖层，不是截图可独立识别的面板（source: `inferred-implementation`; confidence: 0.83 high; strictness: recommended）。主三栏水平约束来自 [layout-spec.md](layout-spec.md)：303 + 16 + 889 + 11 + 406 + 8 = 1633 px；任一右栏子项最右边为 1620 px，未溢出 1633 px 画布。

## 组件详情与 Token 使用

表中 `color.*`、`typography.*`、`spacing.*`、`borderRadius.*` 均在 [tokens.json](tokens.json) 定义；无未使用或缺失 token。bbox、背景、内容和证据为组件样式契约。图像只定义容器，复杂角色与金属光效仍使用授权 raster 素材。

| 组件 | bbox | 背景/样式 Token | 内容及可见证据 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- | --- |
| TopChrome | 0,0,1633,46 | 透明覆盖 | 左三色圆点、右窗口按钮 | reference-visible | 0.91 high | recommended |
| LeftSidebar | 0,0,303,963 | `color.bg.app`, `spacing.32` | 完整深色左栏；列表与底部账号 | reference-visible | 0.95 high | required |
| BrandIdentity | 20,51,242,68 | 图片素材；透明 | 熊宝头像、熊宝 Agent 和副标题 | reference-visible | 0.95 high | required |
| NewTaskButton | 9,143,287,43 | `color.accent.gold.light` → `color.accent.gold.dark`, `borderRadius.pill`, `typography.size.body` | 金色新建任务按钮及图标 | reference-visible | 0.96 high | required |
| NavItems | 22,198,200,334 | `color.surface.raised`, `typography.size.body`, `typography.weight.body`, `spacing.8` | 对话、智能体、项目等纵向图标/文字 | reference-visible | 0.94 high | recommended |
| RecentList | 11,561,284,298 | `color.bg.app` | 最近对话标题和选中行 | reference-visible | 0.90 high | recommended |
| AccountDock | 24,884,261,69 | `color.bg.app` | 用户头像、名字、登录状态 | reference-visible | 0.91 high | recommended |
| MainPanel | 319,47,889,903 | `color.bg.center`, `color.border.subtle`, `borderRadius.panel`, `spacing.12` | 中央圆角任务对话框 | reference-visible | 0.96 high | required |
| TaskTitle | 320,48,886,66 | `color.text.primary`, `typography.size.h1`, `typography.weight.heading`, `typography.lineHeight.title` | 任务标题和右侧操作图标；字号为估计 | reference-visible | 0.94 high | required |
| ChatTimeline | 320,114,886,665 | `color.bg.center` | 消息流可见区域；滚动容器本身由实现推断 | screenshot-inferred | 0.76 medium | recommended |
| MetadataText | 350,128,790,82 | `color.text.muted`, `typography.size.caption`, `typography.weight.body`, `typography.lineHeight.caption` | 发言者、时间、短消息行 | reference-visible | 0.88 high | recommended |
| MessageCard | 414,263,760,491 | `color.surface.card`, `color.border.subtle`, `borderRadius.card`, `spacing.24` | Agent 大块深色回复卡 | reference-visible | 0.95 high | required |
| BodyText | 433,280,723,285 | `color.text.primary`, `typography.size.body`, `typography.weight.body`, `typography.lineHeight.body`, `spacing.24` | 回复卡内多段中文正文；内部 bbox 估计 | reference-visible | 0.79 medium | recommended |
| AttachmentCard | 433,577,302,62 | `color.surface.raised`, `spacing.8` | README.md 文件名、文件图标及预览/复制 | reference-visible | 0.91 high | recommended |
| SuccessStrip | 433,655,723,41 | `color.status.success` | 绿色完成图标、完成文字与展开箭头 | reference-visible | 0.92 high | recommended |
| Composer | 336,779,852,131 | `color.surface.composer`, `color.border.subtle`, `borderRadius.card`, `spacing.16` | 提示词、多枚功能胶囊和右侧圆形发送钮 | reference-visible | 0.95 high | required |
| RightRail | 1219,47,406,903 | `color.bg.app`, `color.border.subtle`, `borderRadius.panel`, `spacing.12` | 独立右侧建议容器 | reference-visible | 0.95 high | required |
| Search | 1233,59,381,39 | `color.surface.raised`, `color.text.muted`, `typography.size.caption`, `typography.weight.body`, `borderRadius.pill` | 搜索框、占位文字和通知铃 | reference-visible | 0.93 high | recommended |
| HeroImage | 1235,112,385,199 | 授权 raster 素材 | 熊宝人物及金色光线；裁切容器 | reference-visible | 0.90 high | required |
| WelcomeCard | 1231,311,383,205 | `color.surface.card`, `color.text.primary`, `typography.size.h2`, `typography.weight.heading`, `borderRadius.panel`, `spacing.16` | 问候标题与三枚快捷卡 | reference-visible | 0.94 high | recommended |
| RecommendedAgents | 1231,528,383,185 | `color.surface.card`, `color.text.primary`, `typography.size.h3`, `typography.weight.subheading`, `borderRadius.panel`, `spacing.16` | 推荐智能体两列卡 | reference-visible | 0.94 high | recommended |
| BrandBanner | 1231,733,383,111 | 授权 raster 素材 | 熊宝 Agent 品牌横幅与右侧箭头 | reference-visible | 0.94 high | recommended |

组件实现须保持真实按钮、输入、滚动与焦点行为；表中尺寸只对 1633×963 截图负责。没有截图证据的终端、项目四页签和云任务容器不在本树中。

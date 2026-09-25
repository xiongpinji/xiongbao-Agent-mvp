# 熊宝-Agent 参考效果图视觉实现契约

## 事实来源

| 项目 | 值 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- |
| 参考文件 | [xiongbao-chat-ui-reference.png](xiongbao-chat-ui-reference.png)，用户提供的 UI 前端效果图 #3 | reference-visible | 1.00 high | required |
| 提取日期 | 2026-09-25 | inferred-implementation | 1.00 high | recommended |
| 基准视窗 | 1633 × 963 px | screenshot-estimated，文件尺寸实测 | 1.00 high | required |
| UI 类型 | `application-dashboard`：可见导航、任务对话和右侧建议栏 | reference-visible | 0.96 high | required |
| 水平结构 | 左 303 + 间距 16 + 中 889 + 间距 11 + 右 406 + 右余量 8 = 1633 px | screenshot-estimated | 0.84 high | recommended |
| 低置信度布局 | 窄屏三栏变化未知 | screenshot-inferred | 0.54 low | flexible |

本规格的唯一视觉事实来源是上述 PNG。用户提供的 [角色设定图](xiongbao-mascot-variants.png) 是候选素材，logo 已在 `dashboard/public/xiongbao-logo.png`（与用户附件字节一致）；它们不作为这张 UI 中项目页布局的证据。WorkBuddy 实机项目空间的动态、计划、任务、资产和配置状态另见仓库 [逐页记录](../WORKBUDDY_LIVE_UI_AUDIT.md)；两类证据分别验收。

## 可见元素与实现

- 左栏：品牌头像、金色新建任务、主导航、最近会话和账户区；bbox 与文字证据见 [visual-analysis.md](visual-analysis.md)（source: reference-visible；confidence: 0.95 high；strictness: required）。
- 中栏：任务标题、消息流、回复卡、附件、完成状态与输入框；各区尺寸见 [layout-spec.md](layout-spec.md)（source: reference-visible/screenshot-estimated；confidence: 0.84–0.96 high；strictness: required）。
- 右栏：搜索、熊宝人物、欢迎快捷卡、推荐智能体和品牌横幅；组件到 token 的映射见 [component-tree.md](component-tree.md)（source: reference-visible；confidence: 0.90–0.95 high；strictness: recommended）。
- 色彩、字级、间距与圆角以 [tokens.json](tokens.json) 的 `$extensions` 元数据为准；颜色是取样基线，不等于原产品的 CSS 源值（source: screenshot-estimated；confidence: 0.68–0.93 medium/high；strictness: recommended）。

## Asset Strategy

复杂熊宝角色和金色光效使用用户授权的 raster 素材；三栏、卡片、输入和图标使用 HTML/CSS/SVG。主视觉裁切源及右下横幅是否存在独立文件仍需核对；详见 [implementation-risks.md](implementation-risks.md) 与 [human-review-needed.md](human-review-needed.md)（source: reference-visible/screenshot-inferred；confidence: 0.64–0.95 medium/high；strictness: required for character artwork）。

## 能力边界与验收

静态参考只能确定可见画面，不能证明滚动机制、鼠标与键盘状态、窄屏断点、文件操作或项目空间业务行为。桌面实现首验在 1633 × 963 同尺寸截屏，随后测文字/边框对比度、焦点与缩放、1280 × 768、1273 × 634 和窄屏的可达性。详细未决项在 [human-review-needed.md](human-review-needed.md)；风险与脚本核验项在 [implementation-risks.md](implementation-risks.md)（source: inferred-implementation；confidence: 0.90 high；strictness: required）。

## 文件索引

| 文件 | 用途 |
| --- | --- |
| [visual-analysis.md](visual-analysis.md) | UI 类型、可见元素 bbox、颜色/字体/间距、被拒绝的推断 |
| [layout-spec.md](layout-spec.md) | 三栏与局部布局、溢出关系、单视窗约束 |
| [component-tree.md](component-tree.md) | 组件结构、bbox 与 token 使用 |
| [tokens.json](tokens.json) | DTCG 风格 token，逐项注明来源、置信度与严格度 |
| [implementation-risks.md](implementation-risks.md) | 视觉实现风险及验证方法 |
| [human-review-needed.md](human-review-needed.md) | 低置信度与人工复核清单 |

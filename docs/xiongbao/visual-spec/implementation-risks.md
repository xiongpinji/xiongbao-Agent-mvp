# Implementation Risks · 熊宝-Agent 参考效果图

本文件只评估参考图 #3（1633 × 963）的任务对话态。风险级别是实现判断，不代表截图已经证明相应的运行行为。项目空间的真实行为以 `docs/xiongbao/WORKBUDDY_LIVE_UI_AUDIT.md` 的实机记录另行验收。

## 高风险

| 项目 | 风险与影响 | 处理与验收 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- |
| 项目空间与对话效果图混淆 | 图中可见三栏任务对话，没有项目四页签。若照图直接制作项目页，会虚构布局及流程 | 分开维护任务对话视觉基准和 WorkBuddy 实机项目空间状态；项目页逐态截图与流程验收 | screenshot-inferred | 0.96 high | required |
| 立体熊宝素材还原 | 头像、右栏主视觉及横幅有复杂角色、服装和金色光效；纯 CSS 无法等价还原 | 采用用户提供且有权使用的透明底素材，按可见容器裁切；逐一检查头像、主视觉和横幅，不把整张 UI 截图当背景 | reference-visible | 0.95 high | required |
| 暗色文字与焦点可访问性 | 灰色元数据、提示字与边框对比可能不足，静态图无法验证 hover、focus、键盘操作 | 在真实浏览器测 WCAG 对比度并检查键盘路径、可见焦点和缩放后布局 | screenshot-inferred | 0.84 high | required |

## 中风险

| 项目 | 风险与影响 | 处理与验收 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- |
| 1633 px 基准外的布局 | 唯一截图无法确定小视窗三栏的隐藏、折叠和滚动策略 | 先固定桌面基准，再在 1280 × 768、1273 × 634 和窄屏实机状态复核；不要把推断断点当原设计事实 | screenshot-inferred | 0.54 low | flexible |
| 字体、字重和行高 | 截图经过栅格化，不能确证字体家族；长段中文换行受字体影响很大 | 用项目已有字体栈先渲染，对比标题、正文、导航和消息卡的行数及字宽 | screenshot-estimated | 0.69 medium | recommended |
| 品牌横幅原图 | 图中横幅像独立合成素材，所给 logo/角色设定图未证明有对应横幅文件 | 核对可用素材；缺失时以授权角色图重组横幅，并单独作视觉验收 | screenshot-inferred | 0.68 medium | recommended |
| 对话长内容和输入框伸缩 | 参考图只显示一条长回复与固定高度输入区，不证明滚动、长附件或多行输入的极端状态 | 保持消息流可滚动、输入区可用；用长消息、多附件、缩放和窗口缩小验证不遮挡操作 | screenshot-inferred | 0.77 medium | recommended |
| 微观间距和颜色 | 某些边界处是渐变或抗锯齿，像素采样值不一定是 CSS 原值 | 把 token 作为首轮基线，再做同视窗截图差分与人工校准 | screenshot-estimated | 0.82 high | recommended |

## 需脚本验证

| 项目 | 当前状态 | 验证方式 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- |
| bbox 与同级宽度 | 主要区域估算；303 + 16 + 889 + 11 + 406 + 8 = 1633，未溢出 | 从参考 PNG 和浏览器截图量边界，脚本检查父子 bbox 与裁切 | screenshot-estimated | 0.84 high | recommended |
| 色值与对比度 | 平坦区已取样；渐变、文字边缘未验证 | 像素采样 + WCAG 对比度脚本，逐态浏览器测色 | screenshot-estimated | 0.83 high | recommended |
| 视觉回归 | 尚无实现截图，因此没有差分得分 | 同尺寸、同缩放的 Playwright 截图与差分；人工核对角色素材裁切 | inferred-implementation | 0.90 high | required |
| 响应式溢出 | 仅有 1633 × 963 原图，没有其他宽度参照 | 实机 1280 × 768、1273 × 634 及窄屏的滚动和操作可达性检查 | screenshot-inferred | 0.54 low | flexible |

## 低置信度汇总

| 项目 | 阶段 | Confidence | 影响范围 |
| --- | --- | --- | --- |
| 三栏在窄屏的折叠/隐藏规则 | 布局推断 | 0.54 low | 所有栏位与输入框 |

字体、横幅和品牌原图属于中置信度未决项，也列入 [human-review-needed.md](human-review-needed.md)。

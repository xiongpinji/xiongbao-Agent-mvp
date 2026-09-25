# Layout Specification · 1633 × 963 桌面基准

## 整体布局

- **UI Type**: `application-dashboard`；source: reference-visible；confidence: 0.96 high；strictness: required。
- **容器**: 1633 × 963 px 截图坐标；source: screenshot-estimated（文件尺寸实测）；confidence: 1.00 high；strictness: required（仅该基准）。
- **布局模式**: 三栏 flex/grid，顶部窗口控件为覆盖层；source: screenshot-inferred；confidence: 0.84 high；strictness: recommended。
- **水平分配**: 左栏 303 + 左间距 16 + 中央 889 + 右间距 11 + 右栏 406 + 右边距 8 = 1633 px；source: screenshot-estimated；confidence: 0.84 high；strictness: recommended。三个可见栏位均不溢出画布。
- **响应式**: 此图只有一个桌面尺寸。窄屏的折叠、隐藏、滚动与保留顺序是 `inferred-implementation`，confidence: 0.54 low，strictness: flexible，必须在另一实机视窗确认。

## 区域规格

| 区域 | 定位/层级 | bbox x,y,w,h | 溢出与圆角 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- | --- | --- |
| 顶部窗口控件 | overlay / z 30 | 0,0,1633,46 | visible / 无圆角 | reference-visible | 0.91 high | recommended |
| 左导航栏 | grid 第一列 / z 0 | 0,0,303,963 | 列表滚动推断；无外角 | reference-visible | 0.95 high | required |
| 中央任务面板 | grid 第二列 / z 1 | 319,47,889,903 | 内部纵向滚动；约 16px 圆角 | reference-visible | 0.95 high | required |
| 中央标题栏 | 中央 flex 首段 / z 1 | 320,48,886,66 | 不滚动；上角随父级 | reference-visible | 0.92 high | required |
| 中央对话流 | 中央 flex 弹性段 / z 1 | 320,114,886,665 | 纵向滚动推断 | screenshot-inferred | 0.76 medium | recommended |
| 回复卡 | 对话流内 / z 1 | 414,263,760,491 | 内容可能伸长；约 18px 圆角 | reference-visible | 0.94 high | recommended |
| 中央输入框 | 中央底部 / z 2 | 336,779,852,131 | 文字换行；约 18px 圆角 | reference-visible | 0.95 high | required |
| 中央底部提示 | 中央底部 / z 1 | 560,917,370,21 | 不滚动 | reference-visible | 0.77 medium | flexible |
| 右建议栏 | grid 第三列 / z 1 | 1219,47,406,903 | 内部纵向滚动推断；约 16px 圆角 | reference-visible | 0.94 high | required |
| 右搜索与通知 | 右栏顶部 / z 2 | 1233,59,381,39 | 搜索框约 20px 胶囊 | reference-visible | 0.91 high | recommended |
| 熊宝主视觉 | 右栏内容 / z 1 | 1235,112,385,199 | raster 裁切，超出须遮罩 | reference-visible | 0.90 high | required |
| 欢迎卡 | 右栏内容 / z 2 | 1231,311,383,205 | 约 16px 圆角 | reference-visible | 0.92 high | recommended |
| 推荐智能体卡 | 右栏内容 / z 2 | 1231,528,383,185 | 约 16px 圆角 | reference-visible | 0.92 high | recommended |
| 底部品牌横幅 | 右栏内容 / z 2 | 1231,733,383,111 | 约 14px 圆角 | reference-visible | 0.90 high | recommended |

## 局部布局关系

| 父元素 | 子元素排列与尺寸 | Source | Confidence | Strictness |
| --- | --- | --- | --- | --- |
| 左栏 303px | 内边距约 9–24px；品牌、43px 新建任务、纵向导航、最近列表、底部账户 | screenshot-estimated | 0.81 high | recommended |
| 中央 889px | 标题约 66px；对话流占剩余空间；输入框距外框左右约 17–18px；底部提示居中 | screenshot-estimated | 0.83 high | recommended |
| 回复卡 760px | 内边距约 18–20px；正文、文件卡、状态条、底部反馈操作纵向排列 | screenshot-estimated | 0.78 medium | recommended |
| 右栏 406px | 内边距约 12px，内容卡宽约 383px；主视觉、欢迎卡、推荐卡、品牌横幅逐块排列 | screenshot-estimated | 0.84 high | recommended |

## 验收边界

这张图只定义桌面任务对话态，不能用于推断项目四页签的像素 bbox。项目空间真实 UI 结构与本图的暗色三栏语言需结合 `docs/xiongbao/WORKBUDDY_LIVE_UI_AUDIT.md` 另做 1273×634、1280×768 和窄屏的状态对照。动效、实际滚动容器、字体家族、品牌素材的完整原图均不由静态截图证明。

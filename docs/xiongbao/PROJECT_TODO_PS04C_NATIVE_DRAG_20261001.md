# PS-04C 看板、甘特与日历：真实拖拽局部验收

日期：2026-10-01。固定源码 `3bd48b982cdfed79d6e79308c3052e9138e81b32`。

Native V3 原始运行证据独立审查：GO。结论仅接受已批准 PS04C C1/C2 的本地合成 PostgreSQL / HTTP / Chromium 原生 HTML 拖拽切片，实际视口为 1440×1000。固定来源为 3bd48b982cdfed79d6e79308c3052e9138e81b32；审查未启动浏览器、服务、测试或 provider，也未改业务源码、方法或既有记录。

实际运行完成 16/16 阶段：7 个 owner 正向动作、2 个 owner 无操作写入、3 个 member 对其他创建者且未指派待办的拒绝动作。两套隔离 context 均通过真实登录页及鼠标滑块完成登录，登录前 auth_token 为空；API 登录仅用于准备合成夹具，未预载浏览器认证。四条待办及三个共享定义由 API 夹具创建，不计为 UI 创建验收。

逐项比对原始请求、响应、PG 与刷新证据后，7 条 PATCH 均为 200，请求严格等于目标变化加 expected_version；DTO 仅变化目标字段、version +1 与 updated_at。PG 顺序快照只改变对应行，各动作恰追加一个 project.todo_updated 事件，原事件前缀与其他行保留。最终四行版本依夹具顺序为 [2,4,3,2]，单开始日期平移的 due_date 仍为 null。四个标签关联、目录、全部五个视图及共享配置状态不变；待办事件从 4 增至 11。

7 次真实页面刷新保存了 15 个必要查询响应，其中 14 个 limit=50 数据页及 1 个 limit=1 统计查询。每次均核对 view/version/catalog，目标同 ID/version/fullDTO 存在于数据页，DOM 的状态、日期或甘特闭区间 span 相符，且捕获响应属于对应动作阶段。全局有 50 个 query 请求、37 个捕获响应（均为 200）、13 个请求未保存对应响应；这 13 个响应结果未验，不声称 50/50 HTTP 成功，也不以 network_failures=0 替代缺失响应。必要的七个保存与刷新闭环都有证据，因此该限制不阻断本切片 GO。

9 次 owner 手势（含两次 no-op）均由 page.mouse 触发，各有一个 isTrusted=true 的 dragstart/drop；drop MIME、具体来源、目标日期/列、坐标和实际元素命中均一致。3 次 member 尝试仅产生 trusted mousedown/mouseup，draggable=false 的断言在冻结方法真实执行路径，未出现 dragstart/drop；quiet window 内 HTTP 读回及整份 PG 快照不变。这是对应未指派他人待办的 UI 拒绝与无写证明，不代表全部角色组合或 API 403 矩阵。只有七条预期 PATCH，额外浏览器写入为 0。

甘特实际查询窗口为 server_today−14 至 +42 的 57 天闭区间；工具栏及全部日期 headers 与该序列一致，鼠标 anchor/落点按实际轨道映射，平移和两端调整后 span/日期相符。日历读取 42 个唯一连续日期并验证本次来源/目标曝光与命中，开始日期依据为临时调整，刷新恢复已保存的截止日期依据；本批不补充全部 42 格键盘可达或三种脏态导航验收。所有操作探针 document scroll 为 0，使用真实固有滚动，未注入事件、修改 CSS 或产品状态来代替操作。

33 张实际 PNG 的字节数、SHA-256 和 1440×1000 尺寸全部匹配，代表性的 11 张已目视核对。图片仅作为本地原始输入，报告只发布统计、名称和散列，不附图片、账号、凭证、夹具对象 ID、请求正文或数据库连接信息。页错误、网络失败、外连观察、观察器异常及 API 拒绝外连均为 0；上述 13 个缺响应的限制独立保留。

真实 PostgreSQL 18、schema 35 的只读快照与 API/连接池/owned cluster 身份相符。API、PG 和 browser 退出码为 0；Vite 是 owned Job 清理退出码 2，不记为正常 0。连接池底层关闭一次，删除数据库前 sessions=0，数据库已删除；PG 正常 stop=0/status=3，四个 owned Job active=0、关闭收据为真，API/web/PG 端口均关闭，两个 context 及 browser 均关闭。

来源在运行前后 clean、HEAD 相同，3119 份文件散列前后相同；独立审查时当前 3119 份字节及六方法再次匹配冻结记录，METHODS_V3、EXECUTION_BINDING_V3、method review/contract 均绑定正确。98 个实际使用的本地 QA 输入均限定 QA 根内，逐个原始散列在 JSON 中冻结；来源全映射由已冻结 run-result 输入绑定并另给 canonical SHA-256。V1/V2 原 journey FAIL 与父驱动 FAILED_OR_INCONCLUSIVE、failure HTML/PNG、原版本与审查报告均保留且进入输入散列，不折算为本轮通过。

未验项保留：共享管理设置、完整脏态选择、缩放矩阵、CAS 竞态、服务重启、800/1280 布局重测、WorkBuddy 保存/原生窗口或视觉 1:1、真实账号、付费模型、全量质量及整体 25 项交付。本次 1440×1000 拖拽切片不替代这些证据。

输入 manifest canonical SHA-256：9a3b151538dc2438b95659e76b03699ef82b7fb36211b53b9205cf59a059092c。
来源 manifest canonical SHA-256：72e7d244d21ad1f01426cd00a0ddf39269118c2e2d893a6823a4214b299a1b05。

仓库仅发布[脱敏摘要](evidence/ps04c-native-drag-20261001/summary.json)与[证据散列清单](evidence/ps04c-native-drag-20261001/evidence_manifest.json)。原始请求、PG 快照、截图和凭证不随文档发布。

此批补充既有功能的实际验收，未修改业务源码或重跑全量质量。[此前双窗口修复与质量结果](PROJECT_TODO_PS04C_REQUIRED_VIEWPORTS_20261001.md)仍按其固定来源和已记录 warnings/跳过范围使用。原三条路由保留；依据用户已批准的 PS-04C 承接，由 Codex 与独立只读审查完成本片，GLM 额度恢复后的补审继续未验。

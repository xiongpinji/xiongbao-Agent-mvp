# PS-04C 原生执行台账（2026-09-28）

状态：书面设计已批准；实施计划与三个首批任务包已固定，三名实施代理已按文件分工启动；尚无接受候选或验收结果。

外部 durable plan：`plan-20260928-045530-5cecba`，起点 cdf1a1bc，risk high、migration、max_parallel 3。该 runner 会把实施计划的全部复选步骤解析为 74 项，其 pending 是外部 CLI ledger 状态，不冒充原生代理实时状态。没有向原两条实现路由发 PS-04C 任务，也没有 GLM 结果。

原池精确保留 claude-bailian/qwen3.8-max 与 opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash；review 路由 qwen-code-review/glm-5.3 只读且待恢复后补审。原生 Codex/子代理执行是 [明确书面授权](PROJECT_TODO_PS04C_APPROVAL_20260928.md) 的例外，模型继承当前 Codex 配置，不伪装成 runner job，不自动扩大其他计划。

| 项 | 所有权 | 当前状态与验收 |
| --- | --- | --- |
| M1 | /root/ps04c_m1_migration；034、迁移helper、seed、迁移测试 | 实施中；尚未验收 |
| M2 | /root/ps04c_m2_backend；C1后端repo/service/router、项目/活动共享接线 | 实施中；依赖M1 schema GREEN |
| M3 | /root/ps04c_m3_frontend；C1前端字段、目录、原页面与身份键 | 实施中；真实API验收依赖M2 |
| R1 | Codex独占request.ts/token副作用测试 | 待TDD；不读真实token |
| Q1 | Codex独占严格定义与compactcursor纯验证 | 仅测试/文档准备可提前；业务实现等待C1整合验收 |
| Q2 | 035、名字派生键、备份 | 等C1整合验收及共享迁移文件释放 |
| Q3/Q4 | 视图配置与SQL query | 等Q1/Q2及C1后端接受 |
| Q5 | 五类型共享视图前端 | 等M3释放共享页面、Q4真实query |
| V | 独立质量/真实PG/TCP浏览器/视觉/Git | 待实际候选，未核销 |

每次独立审查绑定 actual tree/文件 SHA，规格与质量分开且有顺序；证据文件会列命令、退出结果和未验边界。本台账不能将局部通过改写为 C1/C2全交付，不能关闭完整PS-04或0/11全项目旅程双验收。

原 checkout 保持 cdf1a1bc 与旧未跟踪030私密任务测试；文件模式仍False/NO-GO；030 UI、045-F待答授权保持独立。仅文档计划批在 Windows 无make环境沿用明确记录的文档hook跳过，不称业务质量门禁通过。业务代码必须另获新鲜验证并独立审查后提交。

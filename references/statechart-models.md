# 状态图模型与重建

仅在核对图、流程建模或维护Skill时阅读。公司研究使用现有CLI与研究指引，不需要TLC、Java、Pele或statechart Skill。

图基于有限研究协议，不是自动研究引擎。pdfRead、角色明确、客户效果能否核准和证据充分等前提由研究者判断；规则通过不能证明披露真实、阅读充分或因果解释正确。

## 模型与结果

模型和视图放在[assets/statecharts/models](../assets/statecharts/models/)；报告在[verification](../assets/statecharts/verification/)。

| 图 | 模型/配置 | 视图 | 模型状态/转移 | 图节点/箭头 |
| --- | --- | --- | --- | --- |
| 六步总览 | CompanyReasoning.tla / CompanyReasoning.cfg | six-step.view.mjs | 307 / 447 | 6 / 10 |
| 详细认知 | 同上 | reasoning.view.mjs | 307 / 447 | 15 / 25 |
| 证据发现 | EvidenceDiscovery.tla / EvidenceDiscovery.cfg | evidence.view.mjs | 89 / 131 | 12 / 18 |
| 研究与缓存 | Workflow.tla / Workflow.cfg | workflow.view.mjs | 148 / 293 | 11 / 15 |
| 回执与依赖 | Mechanisms.tla / Mechanisms.cfg | mechanisms.view.mjs | 239 / 794 | 14 / 15 |

六步与详细图检查六项不变量及无死锁；证据发现检查七项不变量及无死锁；后两图分别检查四项、七项不变量及无死锁。均无违反所列规则的路径。

六步与详细认知展开一轮回退，证据发现展开一个问题的一轮扩搜；实际研究不限一轮。客户效果可以是正面或负面；“能评估”不等于“实际受益”。程序附图只展开选定共享依赖、一个文本块及一轮输入变化。

六步、详细认知、证据发现、研究缓存、回执依赖图分别折叠321、52、28、47、299个同标签步骤。六步保留关键回退并合并有据与有限交付；完整差异看详细认知图。无死锁不保证现实研究一定在有限时间内完成。

## 重建

图使用[statechart](https://github.com/backnotprop/pstack/tree/f104aa8f821a3f7397318b0dc8ad2f7e4a1f8bc7/skills/statechart)生成，来源提交 f104aa8f821a3f7397318b0dc8ad2f7e4a1f8bc7。安装该工具后，在Skill目录执行以下命令，先将模型复制到包外新scratch；每次运行使用新输出目录：

```sh
cfa_graph_scratch="$(mktemp -d /tmp/cfa-statechart.XXXXXX)"
cp assets/statecharts/models/* "$cfa_graph_scratch/"
cd "$cfa_graph_scratch"
"$HOME/.codex/skills/statechart/scripts/statechart" CompanyReasoning.tla --config CompanyReasoning.cfg --view six-step.view.mjs --out six-step-result
```

其余图按表中模型、配置和视图替换，使用新的 `--out`。SVG用Pele 1.9.0渲染，选项为 `limit: 1_000_000, outputLimit: 8_000_000, maxEdges: 10_000`，遵循statechart的render参考。执行重建不写原件、研究账本或SQLite。

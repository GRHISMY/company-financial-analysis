# 股东回报事件账本

研究分红或回购时使用。AI核准法律主体、公告/报表含义和实际执行证据；CLI负责日期、原币金额、来源绑定、去重和同口径汇总。唯一采用账本为案例内 `analysis/shareholder-returns.json`，报告及Excel引用它；负债变动桥、混合筹资现金仍保留自己的类别。

## 操作

```sh
python3 scripts/cfa.py returns template --kind dividend
python3 scripts/cfa.py returns put --case /path/to/case --file /path/to/event.json
python3 scripts/cfa.py returns validate --case /path/to/case
python3 scripts/cfa.py returns summary --case /path/to/case --start 2024-01-01 --end 2024-12-31
python3 scripts/cfa.py returns render --case /path/to/case --output /path/to/case/analysis/returns.md
```

`put`接受一个事件、事件数组或含`events`的对象；完整校验后原子写入。通过统一CLI导出时，输出相对案例目录解析，且与生成时的账本、manifest及脚本版本绑定；依赖在生成中变化会中止并保留旧产物。无`--output`查询保持只读。相同内容重复录入不变；复核后修改已有事件使用`--replace`，旧值留在`revisions`。实际选项以`--help`为准。直接运行`scripts/returns_ledger.py`提供相同子命令。

## 事件结构

|字段|含义与允许值|
|---|---|
|`event_id` / `economic_id`|记录ID / 实际经济事项ID；同一支付的多份证据附在一个事件，不建立重复支付|
|`kind`|`dividend`、`market_repurchase`、`restricted_share_repurchase`、`treasury_transfer`、`cancellation`、`liability_movement`、`mixed_financing`|
|`stage`|`proposed`、`declared`、`executed`、`cash_paid`、`observed`|
|`period_basis`|`cash_payment`、`plan_execution`、`earnings_year`、`liability_movement`、`noncash_change`|
|`aggregation`|`single`单笔、`period_total`期间现金合计、`plan_total`跨期方案执行累计|
|`start` / `end` / `cash_date`|真实期间边界，格式YYYY-MM-DD；单笔付现三者为同一天，期间合计`cash_date=null`|
|`earnings_year`|利润归属年度字符串或null，独立于支付期间|
|`payer`|`listed_parent`、`subsidiary`、`group`、`unknown`|
|`payer_entity`|子公司付款时填写具体法律实体；母公司/集团默认按案例代码绑定，分别汇总|
|`recipient`|`listed_shareholders`、`minority_shareholders`、`employees`、`selling_shareholders`、`mixed`、`unknown`|
|`value` / `precision`|原币金额和量化步长，用十进制字符串；未知金额`null`并给`unknown_reason`，明确零为`"0"`|
|`currency` / `fee_basis`|明确三字母币种；`included`、`excluded`、`not_applicable`、`unknown`表示交易费用口径|
|`sources`|归档PDF的`source_id`、从1开始的物理`pages`、证据`role`；`put`自动补SHA256，后续验证原件哈希|
|`evidence_status` / `explanation`|`supported`、`conflicting`、`incomplete`；后两类必须说明缺口，汇总保留排除原因|
|`alternatives` / `adoption_reason`|不同披露金额分别保留`value/source/reason`，给出采用理由；未解决冲突保持`conflicting`|

证据`role`为`financial_statement`、`implementation_notice`、`plan_notice`、`liability_bridge`或`other`。`cash_paid`需要直接报表或实施证据，单独的预案/负债桥不能建立已付款事实。代码只核已填写分类的一致性，公告是否足以证明支付仍由AI阅读判断。`precision="0"`仅用于已确认精确值，文件显示舍入金额使用实际步长。

## 汇总边界

- 默认汇总`cash_paid + cash_payment`的纯现金分红、市场回购和限制股退出回购，并分别保留类别、主体、对象、币种和费用口径；不存在跨组总额，也不做汇率换算。
- `--measure execution`单独观察已执行方案累计。跨年回购方案总额不进入当年现金汇总；注销、旧库存转户、应付股利变动与含利息筹资整行也不进入纯回报。
- 查询期间只能完整包含事件窗口；跨界期间合计标为`partial_overlap_not_prorated`，不按月份比例估算。现金记录的截止日不能晚于案例截止日。
- `aggregation_status`区分`no_eligible_events`（没有合格事件）、`amount_incomplete`（合格事件有未知金额）、`recorded_events_aggregated`（已录入合格事件已汇总）；`eligible_events`给出数量。空组或`status=ready`不证明支付为零或资料已经收齐。
- 同组未知金额使`total=null`，同时展示`known_subtotal`。`complete`只表示已录入事件金额齐全，不证明已找齐所有公司支付。
- 同一经济ID或期间总额与单笔支付重叠会阻止写入；选择期间总额或组成事件作为采用口径，另一披露附为证据。执行金额与实际现金保持不同口径。

格力2024的应付股利现金减少131.4374亿元，不能机械减少数股东现金推上市股利；直接实施金额131.4223亿元独立采用。类似地，“股利、利润或偿付利息”整行属于`mixed_financing`，纯回购金额以附注/执行公告核准，含费现金与不含费执行金额分列。

退出码：0表示写入/查询成功，1表示参数或输入不合约，2表示已保存账本验证失败或账本缺失。汇总状态`empty`明确表示尚未录入事件，不代表支付为零。

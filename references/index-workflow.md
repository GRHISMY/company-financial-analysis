# SQLite证据检索与按需研究

从`cfa.py index --help`进入；子命令的`--help`是参数入口。此流程使用本机SQLite及主AI/subagent，无需本地模型。索引由原件清单、逐页文本、阅读底稿和发现账本重建，原文与AI研究记录分别标识。

## 建库与接续

案例目录未知时先用`index status`列出本库的公司、代码及目录，再选择案例接续；库尚不存在时按新研究初始化和同步。

新公司先`init`、取得并归档原件，再提取及同步。已有研究直接导入现有成果：

```sh
python3 scripts/cfa.py source extract --case /absolute/case
python3 scripts/cfa.py index sync --case /absolute/case
python3 scripts/cfa.py index status --case /absolute/case
```

默认库在macOS本机`~/Library/Caches/company-financial-analysis/evidence.sqlite3`，供后续会话复用；活跃库保持本机运行，iCloud中的原件与JSON底稿保留可重建依据。同步多个案例可重复`--case`；独立验证可用`--db`指定临时库，或以`CFA_INDEX_DB`选择库。

同步只更新检索缓存，不改原件、采用值、研究结论或阅读回执。提取、阅读底稿、发现或索引规则改变后重新同步；查询返回`missing`或`stale`时按提示补齐或刷新，再使用结果。更正版与旧版的来源身份分开，引用时落实本次采用的版本。

## 查询、展开与覆盖

先确定公司和报告期，再查询已知事实、专题及原文。跨公司检索保留各自代码；报告期与正文提及的比较年度分别核准。

```sh
python3 scripts/cfa.py index search --code 000651 --start-year 2020 --end-year 2026 \
  --query '受限资金 保证金 质押 冻结' --limit 12 --max-chars 12000
python3 scripts/cfa.py index fetch --hit-id 命中返回的ID --max-chars 12000
python3 scripts/cfa.py index coverage --code 000651 --start-year 2020 --end-year 2026
```

结果继承来源ID、原件摘要、PDF物理页和原文位置。原文命中用于核对披露，AI笔记命中用于恢复研究线索与限制，机械关键词导航作为后备；相关性排序不证明事实成立。已有专题入口后，可用`search --kind original`聚焦原文。`fetch`展开命中关联的原页，不确认阅读；需完整通读时使用[PDF阅读CLI](pdf-reading.md)，疑义表格看原件。

检索采用关键词及主题扩展。AI负责将问题拆成可找的概念，例如“现金能否自由使用”可追查受限资金、保证金、冻结、资金归集和成员单位存款。检索不足时改写词项、检查相关章节及相邻页，未命中只表示本次未找到，不据此写“未披露”。

按公司×年份查看覆盖，再决定是否继续研究：

- `report_types`列实际已归档的报告类型计数；用报告类型筛选核对年报、半年报、季报及专项公告。
- `not_archived`：需要的年度/报告尚未入库，先核对资料范围。
- `no_match`：该组有材料，但本次查询未命中；扩展词项或展开原文。
- `omitted_by_budget`或截断：结果受条数/字符预算限制，缩小查询范围或增加预算继续。
- 阅读未完成、视觉待核、专题证据缺口：分别保留待办，不因命中或缓存新鲜而消除。

完整JSON预算以字符计，按工具输出容量选择`--max-chars`。`fetch`可按返回的页和偏移继续，避免把截断片段当成完整页面。全局前若干条不能代表十年已覆盖；分别检查缺报告、未读与无匹配。

## 缺口再交给subagent

先复用同一来源版本的现有底稿。需要新阅读时，将任务限定到报告、页段或专题，给出来源ID、报告期间、研究问题及已知缺口。每份报告指定一个基础读稿写入者；专题研究者提交独立输入，由该写入者合并，保留事实、推断、替代解释和缺口。

用`source reading-template`取得实际来源的阅读格式，实际阅读后写入既有阅读JSON并经`source coverage`校验；重要发现通过`finding put`写入唯一账本。完成后再次`index sync`，使下一次查询复用成果。原文入库、搜索及摘要生成都不补写已读状态，旧阅读记录也不追认为新投递回执。

局部查询在证据支持结论或明确保留缺口时收尾。完整公司研究仍按[分析指南](analysis-guide.md)通读逐期业务、治理、重大事项及财务报告，并完成母子公司、资本配置、利润与现金四条主线；SQLite和subagent帮助接续与定位，专题覆盖以实际阅读和证据为准。

# 正式入口、发现与可接续状态

`scripts/cfa.py --help` 列命令；子命令的 `--help` 提供参数。旧脚本仍可独立运行，新研究优先使用统一入口。

## 正式产物

`manifest.json`记录原件；`case-state.json`指定每类正式产物的唯一文件、SHA256、输入/规则依赖。未注册文件只是候选，文件名含final也不自动采用。

```sh
python3 scripts/cfa.py init --case /absolute/case --company 公司简称 --code 000651 --start 2020-01-01 --end 2026-11-06
python3 scripts/cfa.py status --case /absolute/case --compact --verify-sources
python3 scripts/cfa.py register --case /absolute/case --key adopted_facts --file workpapers/numbers/adopted-facts.json
python3 scripts/cfa.py build --case /absolute/case
```

`register`是在核准后选择正式版本；不会根据目录猜采用值。事实JSON自动绑定可识别原件；自定义输入用`--input`、注册产物依赖用`--depends`、原件用`--source-id`、规则用`--producer`。独立CSV/PDF事实都注册后，`build`才生成输入核对；不复制CSV充当PDF证据。`build`返回检查计数，写出与数值通过分别判断。

源文件、采用值或规则改变时相关产物显示stale；上游过期时拒绝注册下游。报告键使用`report`或`report.*`，同时绑定研究期间与来源清单。`scope`显式更新日期，旧案例`init`保留原期间。

旧研究经`status`只读接入：列候选、原阅读记录和新回执状态；核准文件后再注册。旧阅读JSON不会追认为新CLI投递回执，原件与已发表研究无须批量迁移。

## 先查已有证据

原件归档并`source extract`后，使用`index sync --case <目录>`导入全文、阅读底稿和发现。恢复研究先看案例状态和索引状态；SQLite尚无本案例或依赖过期时重新同步。默认查询先查库，再展开原文，证据缺口才交给subagent。跨年比较检查公司与年份覆盖，具体流程见[证据检索](index-workflow.md)。

SQLite是可重建的本机查询层，正式依据仍在`manifest.json`、逐页提取、阅读JSON和发现账本。已有底稿可直接复用，不为入库重读整批PDF；入库及命中均不自动建立阅读回执。读稿、发现或来源版本更新后再同步，查询才能接续新成果。

## 一个发现只维护一个当前版本

`analysis/findings.json`保存重要发现。`finding template`生成字段；`put`接收一个发现、列表或含`findings`的对象，校验原件页/哈希和计算版本。相同内容不变，更改保留历史。多个旧计算引用需同时更新时提交批次。

```sh
python3 scripts/cfa.py finding template
python3 scripts/cfa.py finding put --case /absolute/case --file /absolute/finding-input.json
python3 scripts/cfa.py finding validate --case /absolute/case
python3 scripts/cfa.py finding render --case /absolute/case --output analysis/findings.md
```

`claim_type`区分事实、计算、推断，`status`区分待查、有支持、有限结论、已解决。`materiality`由AI判断；有限结论须写缺口。计算引用使用artifact key和JSON pointer，绑定SHA与当前值。记录核验不证明解释成立。

保留AI撰写的`analysis/report.md.in`模板，在需要复用发现处写`{{finding:F001}}`：

```sh
python3 scripts/cfa.py finding render --case /absolute/case --template analysis/report.md.in --output analysis/report.md
```

生成段落包含类型、状态、来源、计算、替代解释和缺口；发现变化后报告过期，重新渲染并复核综合论证。输出保护原件、索引、账本及注册输入。

股东回报另用[事件账本](shareholder-returns.md)。通过统一入口写入/导出会更新正式索引；只查询summary不会改案例。`finding/returns`的`--output`和`finding --template`按案例目录解析；`--file`是外部输入，按shell当前目录解析，建议用绝对路径。`source/data`沿用各自`--help`路径约定。

一页结论可由AI另写、以`report`或`report.*`键注册并绑定findings/returns等真实依赖；展开式发现稿保留完整证据。局部公告研究无须为获得完整状态而补建无关三表。

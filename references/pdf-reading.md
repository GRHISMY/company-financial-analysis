# PDF读取、确认与接续

`cfa.py pdf --help` 查询命令；独立执行入口是 `scripts/pdf_reader.py`。先用 `source extract` 生成按物理页存放的 `pages.json`。搜索、提取与发出文本均不会自动确认阅读。

## 有界输出

```sh
python3 scripts/cfa.py pdf next --case /absolute/case --source-id pdf-实际ID --max-chars 8000
```

每次返回一个完整JSON：`segments` 标记每段的物理页、原文字符起止偏移、页总字符数、`page_complete`、文本及省略依据。长页会按字符拆段，短页可合并；预算包含完整JSON和结尾换行，单位是字符而非token。按工具输出预算选择上限，默认8000字符。

`visual_required_pages` 明示本块涉及的低文字或已标记需视觉/OCR的页。文字交付全部完成后，该列表仍会出现在状态中；文字确认与原页视觉核验分别完成。

完整查看本块并处理必要的原页视觉核验后，使用JSON末尾的两个标识确认：

```sh
python3 scripts/cfa.py pdf ack --case /absolute/case --source-id pdf-实际ID \
  --chunk-id chunk-实际ID --receipt-sha256 完整输出末尾的SHA256
```

已经完整看过上一块时，也可在一次调用中显式确认上一块并取得下一块：

```sh
python3 scripts/cfa.py pdf next --case /absolute/case --source-id pdf-实际ID \
  --ack-chunk-id chunk-上一块ID --ack-receipt-sha256 上一块完整输出末尾的SHA256
```

这两个参数必须成对提供；新发出的下一块仍保持待确认。

未确认时再次 `next` 重放同一块。工具截断时保持未确认；可增加工具输出容量重放，或显式以较小预算从同一断点重新分块：

```sh
python3 scripts/cfa.py pdf next --case /absolute/case --source-id pdf-实际ID \
  --max-chars 4000 --rechunk-pending
```

重新分块保存被替代回执的身份，旧回执不可再确认，已确认进度不受影响。确认只记录AI/人的阅读声明，不能证明理解、表格列对齐或扫描页内容；低文字页、异常表格及关键疑义仍需原PDF渲染/OCR核查。

## 断点与原件身份

```sh
python3 scripts/cfa.py pdf status --case /absolute/case
```

状态文件位于 `workpapers/pdf/<source_id>/reader-state.json`，含原件SHA256、提取正文内容摘要、已确认交付文本及待确认回执。历史区间、实际交付文本和去重依赖均重新核对回执摘要；修改区间不能伪造完整交付。一页的全部字符均确认后才进入 `completed_page_ranges`；空文字页也需显式经过。`delivery_complete` 与语义阅读底稿、`visual_required_pages` 的视觉核验是独立状态。

原件、提取页正文/边界或有效视觉标记变化会拒绝继续旧进度；仅 `extracted_at`、JSON缩进/键排序等元数据变化不影响进度。原件变化应恢复原件或归档新版本；正文变化需要重读时可保留旧进度并重新开始：

```sh
python3 scripts/cfa.py pdf reset --case /absolute/case --source-id pdf-实际ID \
  --reason '提取文字已重新生成，需要按新版本从第一页复读'
```

`reset` 将旧回执文件保存到 `reader-history`，不会改原件或把历史已读声明转成新文件已读。已有研究的旧阅读JSON没有回执时，标为未追踪，不追认成新CLI已经投递。

## 精确重复行

```sh
python3 scripts/cfa.py pdf next --case /absolute/case --source-id pdf-新ID \
  --dedup-source pdf-已确认旧ID
```

仅可复用同案例内已确认回执覆盖的完整原文行。完全相同、至少40字符且不含任何数字字符的行可省略；变化行、数字行、未完整读过的行仍输出。每项省略保留旧原件、回执、页、行、行哈希和目标偏移；原始提取文字不变。旧回执失效或来源改变会使依赖它的阅读状态报错。此功能复用已读相同文字，不能替代新报告的业务、附注及变更判断。

## 搜索定位

```sh
python3 scripts/cfa.py pdf search --case /absolute/case --source-id pdf-实际ID \
  --query 关联交易 --limit 12 --max-chars 6000
```

搜索返回物理页及片段，受字符预算和条数限制，`truncated` 明示是否还有命中；不会修改确认进度。未命中不证明事项不存在。

## 阅读底稿与报告类型

`source extract` 为新文件生成v2阅读底稿，身份按 `checked_code` 和期间校验，`checked_company` 可填写已核实的简称或法定名称；`checked_legal_name` 用于另存全称。

填写前从CLI取得本来源的未读模板、接受的字段及状态枚举：

```sh
python3 scripts/cfa.py source reading-template --case /absolute/case --source-id pdf-实际ID
```

返回的 `template` 保持全部未读，`contract` 列本来源要求及示例形状。`--out 新文件路径` 只保存未读模板，已有文件会拒绝覆盖。现有读稿沿 `existing_note` 路径编辑，保留模板内的来源ID、原件摘要、报告期及专题集合。

身份核准完成时使用 `identity_check.status="read"`；完整通读完成时使用 `whole_report_review.status="read"`，而非 `done`/`complete`。专题使用CLI列出的 `read`、`not_disclosed` 或 `evidence_gap`，未读继续保留 `unread`。`material_findings` 可以为空；每条发现必须有 `pages`、`finding`、`financial_impact`，单独写一句摘要不构成有效条目。

下面仅示范**确实核准身份并读完两页专项文件之后**需要填写的片段；来源绑定字段沿用CLI模板。公司、期间、页码、时间和事实均按实际改写，示例不是对任何真实来源的阅读声明：

```json
{
  "reader": "本次实际阅读者",
  "reviewed_at": "实际ISO时间",
  "identity_check": {
    "status": "read", "pages": [1],
    "finding": "填写封面实际核准的公司、代码与报告期间。",
    "checked_company": "实际观察的公司名称",
    "checked_code": "与本来源核准一致的代码",
    "checked_period": "与本来源核准一致的报告期"
  },
  "sections": {
    "significant_matters": {
      "status": "read", "pages": [1, 2],
      "finding": "填写原件披露的具体事项与证据。",
      "year_over_year_change": "填写可确认的跨期变化；没有可比披露则说明限制。",
      "financial_impact": "说明该事项怎样约束利润、现金或股东回报判断。",
      "evidence_gaps": []
    }
  },
  "whole_report_review": {
    "status": "read", "reviewed_page_ranges": [[1, 2]],
    "unreviewed_page_ranges": [],
    "finding": "填写顺序通读后的实质观察与待核问题。"
  },
  "material_findings": [{
    "pages": [2], "finding": "填写该页的具体重要事实。",
    "financial_impact": "说明它对本次分析结论的影响。"
  }]
}
```

- 年报、半年报：13专题完整记录。
- Q1/Q3：业务、三表、重大事项、治理四项；可追加其他已实际研究的专题。
- 专项公告：重大事项必填，其他相关专题按内容追加。

所需专题由归档 `report_type` 决定，不能编辑为空来通过校验。旧底稿继续按原13专题检查，重提取不覆盖旧记录。任何类型仍要求实际完整通读和正确物理页范围；短报未提供的母公司、关联方、资金限制等细节，在相关发现中明确当期无法更新及最近依据，不将旧期资料当本期事实。

仅缺省 `reading_schema_version` 的旧记录按legacy兼容；显式未知版本会拒绝，不能靠改版本绕过当前合约。

```sh
python3 scripts/cfa.py source coverage --case /absolute/case --summary \
  --out /absolute/case/workpapers/pdf-reading-coverage.json
```

覆盖结果分别列语义记录完整性、证据缺口和文本回执。机器核验记录与来源身份，企业判断由AI完成。

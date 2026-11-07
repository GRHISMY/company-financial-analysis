# 原件取得与归档

取数优先用用户已登录的理杏仁网页导出，从报告期旁PDF图标取得完整报告，见[网页流程](lixinger-workflow.md)。实际格式保留CSV/XLSX；封面、公司、报告期、全文/摘要及比较口径由AI核准。

归档、验证、去重、版本及提取由CLI执行：

```sh
python3 scripts/cfa.py source ingest --help
python3 scripts/cfa.py source extract --case /absolute/case
python3 scripts/cfa.py source coverage --case /absolute/case --summary
```

`ingest --file`接收浏览器下载；`--url --verified-public`只接收已核准的公开原件直链。网页返回验证页或HTML时，记录失败，再找同一披露的交易所、巨潮或公司官方原件。登录会话留在浏览器，不复制cookie或令牌。

`manifest.json`保存原件URL、取得设置、SHA256和身份；同内容去重，不同版本保留。表格在raw/tables，PDF在raw/pdf，提取在workpapers/pdf。提取生成物理页全文、索引及未读底稿，重提取保留旧阅读记录。低文字页和表格歧义需要原页视觉/OCR核验。

全文投递与断点交给[PDF阅读CLI](pdf-reading.md)，索引和搜索用于定位。来源收齐、文本实际读到、关键问题证据充分分别判断；季度未披露的细节保持缺口，不填零或搬用旧期资料作最新事实。

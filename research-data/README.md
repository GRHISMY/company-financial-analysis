# 既有研究数据快照：2026-11-07

[打开完整数据 Release](https://github.com/GRHISMY/company-financial-analysis/releases/tag/research-data-2026-11-07)。研究资料以同一仓库的 Release 附件保存；`git clone` 只获取 Skill 源码和说明，不会自动下载附件。

## 内容

| 附件 | 归档文件数 | 压缩后大小 |
| --- | ---: | ---: |
| [`gree-2020-2026.zip`](https://github.com/GRHISMY/company-financial-analysis/releases/download/research-data-2026-11-07/gree-2020-2026.zip) | 240 | 54.79 MiB |
| [`moutai-2020-2026.zip`](https://github.com/GRHISMY/company-financial-analysis/releases/download/research-data-2026-11-07/moutai-2020-2026.zip) | 182 | 39.39 MiB |
| [`meiya-2020-2026.zip`](https://github.com/GRHISMY/company-financial-analysis/releases/download/research-data-2026-11-07/meiya-2020-2026.zip) | 125 | 22.03 MiB |
| [`comparisons-2020-2026.zip`](https://github.com/GRHISMY/company-financial-analysis/releases/download/research-data-2026-11-07/comparisons-2020-2026.zip) | 58 | 3.92 MiB |
| [`evidence-sqlite.zip`](https://github.com/GRHISMY/company-financial-analysis/releases/download/research-data-2026-11-07/evidence-sqlite.zip) | 1 | 39.82 MiB |

三家公司正式登记的财报及补充公告 PDF 共 49 份（格力 21、贵州茅台 17、美亚光电 11），原始表格 CSV 共 27 份。完整案例同时保留历史 PDF 副本、旧版本、阅读记录、逐页文本、数字核验、计算结果、专题分析、报告、图表和工作簿，以维持旧笔记的来源关系。三公司比较材料也一并归档。

SQLite 快照解压后为 124,989,440 字节，含 3 个案例、49 份报告、4,189 页和 8,829 条检索记录。五个 ZIP 共保存 606 个既有文件。此次只新增压缩包、校验清单与下载说明；研究内容和数据库字节保持原样。排除三份临时 `.archive.lock`。未补建旧案例缺失的 `case-state.json` 或正式 finding 账本，也未重新分析或重建索引。

## 下载、核验与解压

从 Release 下载五个 ZIP、`FILES.json` 与 `SHA256SUMS`，放在同一个目录。macOS / Linux 可先运行：

```sh
shasum -a 256 -c SHA256SUMS
```

校验通过后，把五个 ZIP 解压到同一个自选研究目录。目录结构为：

```text
cases/格力2020-2026/
cases/贵州茅台2020-2026/
cases/美亚光电2020-2026/
comparisons/三公司2020-2026对比/
indexes/evidence.sqlite3
```

`FILES.json` 列出每个 ZIP 内文件的相对路径、原始字节数与 SHA256，也记录附件校验值和省略的临时文件。原书 PDF、个人账户资料与登录凭据未纳入本次快照。

## SQLite 恢复边界

这是保留原始字节的历史快照，部分底稿与 SQLite 含原机的绝对路径。SQLite 的案例 ID、依赖和指纹也与原案例路径绑定。

- 原机上的案例目录和文件保持原位置时，可通过索引命令的 `--db` 或 `CFA_INDEX_DB` 使用解压后的数据库。
- 换机器或换案例目录时，仅设置数据库位置不足以恢复最新版 CLI 的正式检索；原路径缺失会显示 `stale`。明确恢复研究时，使用归档的 PDF、逐页文本、阅读记录与现有 `index sync` 在新位置建立新的缓存库即可，无需重新提取或重读全部 PDF。命令参数以 `python3 scripts/cfa.py index --help` 为准。

此数据包保留历史研究的实际完成度，归档成功和校验通过不代替财务结论的充分性验收。

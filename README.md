# 企业财报分析 Skill

用客户价值解释企业为什么赚钱，再用完整财报与附注判断赚钱的质量、现金兑现、资本配置和股东回报。面向 A 股非金融公司的多年研究与跨公司比较，分析主线参考张新民《火眼金睛读财报》。

**确定的事情交给 CLI 和脚本，需要判断的地方交给 AI。** 完整财报 PDF 是核心证据；CSV/Excel 提供数量骨架，附注解释经济含义。

## 分析什么

- **客户价值与盈利机制**：谁决策、谁付款、谁使用、谁受益；交付什么结果，凭什么保留利润并收现。
- **母子公司**：穿透经营、融资、现金与损失的法律主体，解释合并抵销及母公司支付能力。
- **资产与资本配置**：投入形成了什么资产，多久转为利润和现金，失败投入付出了什么代价。
- **利润质量**：区分产品经营、资金收益、政策支持和会计估计变化。
- **现金质量与股东回报**：拆解收现来源、营运资金、受限资金，核准分红、回购、注销的实际实施。

AI 提出解释、替代解释和反证条件；脚本管理来源哈希、口径、勾稽、计算、逐页阅读状态、发现账本与 SQLite 索引。通过校验不代表研究结论已经成立。

## 安装

把本仓库克隆到 Codex 的 skills 目录。下面命令适用于目标目录尚不存在时：

```sh
git clone https://github.com/GRHISMY/company-financial-analysis.git ~/.codex/skills/company-financial-analysis
```

在 Codex 中调用 `$company-financial-analysis`，例如：“分析某公司最近十年的利润质量、现金质量、资本配置与股东回报”。已有安装时先处理本地修改，再更新仓库。

入口：[SKILL.md](SKILL.md)。详细研究方法按入口中的链接逐步读取。

## 已有研究数据

[下载 2026-11-07 研究数据快照](https://github.com/GRHISMY/company-financial-analysis/releases/tag/research-data-2026-11-07)：三家公司的完整现有案例、原始 PDF、中间过程数据、比较报告及 SQLite。大文件保存在同一仓库的 Release 附件，需单独下载；[内容、校验与恢复说明](research-data/README.md)。

## 运行环境

- Python 3.9 或更新版本，Python 脚本使用标准库。
- Python 所连接的 SQLite 需要支持 FTS5 与 trigram tokenizer；测试包含实际建库验证。
- PDF 提取需要 PATH 中的 Poppler `pdfinfo`、`pdftotext`；需要渲染原页时可使用 `pdftoppm`。macOS 使用 Homebrew 时可运行 `brew install poppler`。
- 图表脚本直接输出 SVG，无需 matplotlib。
- 理杏仁网页导出脚本需要宿主提供相应浏览器 tab API，并由使用者在网页中登录；不是独立无头下载器。见[理杏仁流程](references/lixinger-workflow.md)。

主要验证环境为 macOS。Windows 上进程锁会降级，尚未完成完整平台验证。

## CLI 快速开始

在仓库根目录执行：

```sh
python3 scripts/cfa.py --help
python3 scripts/cfa.py init --case /absolute/path/to/research-case --company 公司简称 --code 000651 --start 2020-01-01 --end 2026-11-07
python3 scripts/cfa.py status --case /absolute/path/to/research-case --compact
```

`--help` 是参数说明入口。研究案例放在仓库外独立目录；按工作流归档原件、提取、阅读、核验与登记发现，然后用 `index sync` 更新查询层。恢复研究先查状态和 SQLite，再展开命中原页；证据缺口才补读或分派 subagent。

SQLite 可以重建，正式依据仍是归档原件、逐页提取、阅读记录和发现账本。默认缓存位于 macOS 的 `~/Library/Caches/company-financial-analysis/`，其他系统使用 `~/.cache/company-financial-analysis/`；可用 `CFA_INDEX_DB` 或索引命令的 `--db` 指定位置。

## 验证

```sh
python3 scripts/cfa.py --help
python3 -B -m unittest discover -s scripts -p 'test_*.py'
```

测试使用临时目录与合成材料，不依赖研究案例。Skill 源码与研究快照分别提供：源码随 Git 克隆，PDF、底稿及 SQLite 从上述 Release 下载。

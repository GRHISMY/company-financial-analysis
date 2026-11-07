# 数字输入、采用值与共享公式

通过`cfa.py data --help`及`data import/reconcile/calculate/formulas --help`查询参数。字段和执行逻辑在脚本中维护。事实采用`financial-facts/v1`，结果采用`financial-calculations/v1`。

## 输入与采用

1. `data import`读取理杏仁CSV，保留公司代码文本、来源行列、科目层级、单位、精度、空缺和零。下载为XLSX时保留原件，按实际工作表转换或构建事实JSON，不改后缀冒充工作簿。
2. 显式mapping选中真实来源格；`import --mapping ... --facts-output ...`产生CSV事实。同名项、符号、单位和重述由AI核准。已有案例可复用结构，来源行号按本次原件核查。
3. PDF事实从独立原页核准。`data reconcile`保留两源原值、精度、差额和原因；不能复制CSV充当PDF核验。AI完成采用判断，保存`adopted-facts.json`。
4. 注册`adopted_facts`、`csv_facts`、`pdf_facts`，再用`build`生成计算和输入核对。比较重述有差异时，用`data reconcile --pairs`显式指定关系，另注册该核对和pairs依赖。

一格事实的最小形状：

```json
{"value":"100000000.23","unit":"CNY","precision":"0.01","kind":"duration","source":{"pdf":"/absolute/case/raw/pdf/original.pdf","sha256":"原件完整SHA256","page":60,"table":"合并利润表"}}
```

期间包含`id,start,end,scope,currency,basis,period_type,fields`；主体为consolidated或parent。保留字段自己的来源版本、原文列名和实际比较日期，同一中报资产比较列与利润比较列可能不是同日。precision是量化步长；未知value用null，明确零附依据，自定义余额字段标`kind: instant`。

财务费用内利息与金融业务利息是不同字段。减值为利润影响（损失负），cash_begin/end取现金及现金等价物合计。原报与后期重述留版本；CFO/净利的主体、期间和业务范围由AI解释。

## 公式只定义一次

```sh
python3 scripts/cfa.py data formulas --output /absolute/formulas.json
python3 scripts/cfa.py data formulas --format excel --bindings /absolute/bindings.json --output /absolute/excel-formulas.json
```

Python计算与Excel字符串共用声明，导出带catalog_sha256。绑定示例：`{"revenue":{"sheet":"Inputs","cell":"B2"},"cost":"Inputs!B3"}`。缺绑定不给公式；单元格空白、文字、错误及非正比率分母返回NA，确认零正常计算。只接受标量单元格，拒绝表达式、范围和外部工作簿。

可编辑Excel调用导出器，不重写第二份系数。勾稽公式提供差额，原始精度下是否通过仍用计算器容差；Excel差额为零不代替来源核准。趋势图可用`scripts/plot_financials.py`。

`data calculate/reconcile`默认退出0表示结果已写出，`--strict`在失败/缺输入时返回2，具体读结果计数。派生单季不生成相减的资产余额，缺检查输入保持缺失。数值、PDF阅读与经营证据分别验收。

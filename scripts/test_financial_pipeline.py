#!/usr/bin/env python3
"""Observable parser and accounting invariants; stdlib unittest only."""
import csv
import ast
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("financial_pipeline", Path(__file__).with_name("financial_pipeline.py"))
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)


def field(value, precision="0.01", kind="duration"):
    return {"value":str(value) if value is not None else None,"precision":precision,"unit":"CNY","kind":kind,"source":{"file":"fixture.pdf","page":1}}


def period(identifier, start="2025-01-01", end="2025-12-31", period_type="annual", **fields):
    return {"id":identifier,"start":start,"end":end,"scope":"consolidated","currency":"CNY","basis":"same-accounting-policy","period_type":period_type,"fields":{k:field(v) for k,v in fields.items()}}


def formula_fixture():
    return period("2025",revenue=100,cost=30,surtax=10,selling=5,admin=4,rd=1,
        finance_expense=-2,finance_interest_income=3,
        financial_interest_income=2,financial_interest_expense=1,financial_fee_income=0,financial_fee_expense=0,
        investment_income=1,fair_value_gain=0,asset_disposal_gain=0,other_income=1,
        credit_impairment_impact=-1,asset_impairment_impact=-2,other_operating_impact=0,
        operating_profit=52,nonoperating_income=1,nonoperating_expense=2,profit_before_tax=51,
        income_tax=10,net_profit=41,attributable_profit=36,minority_profit=5,
        assets=100,liabilities=30,equity=70,cfo=50,cfi=-5,cff=-10,fx_effect=1,
        cash_change=36,cash_begin=10,cash_end=46,capex=6)


def evaluate_excel_subset(formula, cells):
    """Independent arithmetic check for the exporter's small Excel subset.

    This deliberately parses a whitelist, never Python-evaluates formula text.
    It is not an Excel engine; spreadsheet integration remains a separate test.
    """
    def walk(node):
        if isinstance(node,ast.Constant) and isinstance(node.value,(int,float)):
            return fp.D(str(node.value))
        if isinstance(node,ast.Name):
            return cells[node.id]
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):
            return -walk(node.operand)
        if isinstance(node,ast.BinOp):
            a,b=walk(node.left),walk(node.right)
            if isinstance(node.op,ast.Add):return a+b
            if isinstance(node.op,ast.Sub):return a-b
            if isinstance(node.op,ast.Mult):return a*b
            if isinstance(node.op,ast.Div):return a/b
        if isinstance(node,ast.Compare) and len(node.ops)==1 and isinstance(node.ops[0],ast.Gt):
            return walk(node.left)>walk(node.comparators[0])
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
            name=node.func.id
            if name=='IF':return walk(node.args[1]) if walk(node.args[0]) else walk(node.args[2])
            if name=='AND':return all(walk(x) for x in node.args)
            if name=='ISNUMBER':return isinstance(walk(node.args[0]),fp.D)
            if name=='NA':return None
        raise AssertionError(f'Unexpected Excel export syntax: {ast.dump(node)}')
    return walk(ast.parse(formula.lstrip('='),mode='eval').body)


class PipelineTests(unittest.TestCase):
    def test_catalog_and_excel_reproduce_decimal_results_without_second_coefficients(self):
        p=formula_fixture()
        fields=p['fields']
        bindings={key:'A'+str(i) for i,key in enumerate(fields,1)}
        cells={bindings[key]:fp.dec(item['value']) for key,item in fields.items()}
        computed=fp.calc_period(p)
        catalog=fp.formula_catalog()
        exported=fp.excel_formulas(bindings)
        self.assertEqual(exported['catalog_sha256'],catalog['catalog_sha256'])
        for name,item in computed['derived'].items():
            exported_item=exported['formulas'][name]
            self.assertEqual(exported_item['status'],'ready')
            self.assertEqual(evaluate_excel_subset(exported_item['formula'],cells),fp.dec(item['value']),name)
            if 'coefficients' in item:
                self.assertEqual({k:fp.dec(v) for k,v in catalog['definitions'][name]['coefficients'].items()},
                                 {k:fp.dec(v) for k,v in item['coefficients'].items()})
        for name,item in computed['checks'].items():
            result=exported['formulas']['check.'+name]
            self.assertEqual(evaluate_excel_subset(result['formula'],cells),fp.dec(item['difference']),name)
            self.assertEqual(evaluate_excel_subset(result['computed_formula'],cells),fp.dec(item['value']),name)
        # Existing output remains the familiar 49 core / 44 FCF fixture.
        self.assertEqual(fp.dec(computed['derived']['core_profit']['value']),fp.D(49))
        self.assertEqual(fp.dec(computed['derived']['simplified_fcf']['value']),fp.D(44))
        catalog['definitions']['core_profit']['coefficients']['revenue']=99
        self.assertEqual(fp.formula_catalog()['definitions']['core_profit']['coefficients']['revenue'],1)

    def test_excel_missing_is_not_zero_and_denominator_policy_matches_python(self):
        exported=fp.excel_formulas({'cfo':'A1','capex':None},['simplified_fcf'])
        self.assertEqual(exported['formulas']['simplified_fcf']['status'],'missing_bindings')
        self.assertEqual(exported['formulas']['simplified_fcf']['missing'],['capex'])
        self.assertIsNone(exported['formulas']['simplified_fcf']['formula'])
        formula=fp.excel_formulas({'cfo':'A1','capex':'A2'},['simplified_fcf'])['formulas']['simplified_fcf']['formula']
        self.assertEqual(evaluate_excel_subset(formula,{'A1':fp.D(0),'A2':fp.D(0)}),fp.D(0))
        for absent in [None,'', '0', '#N/A']:
            self.assertIsNone(evaluate_excel_subset(formula,{'A1':fp.D(50),'A2':absent}))
        ratio=fp.excel_formulas({'cfo':'A1','net_profit':'A2'},['cfo_to_net_profit'])['formulas']['cfo_to_net_profit']['formula']
        for denom in [fp.D(0),fp.D(-1),None,'']:
            self.assertIsNone(evaluate_excel_subset(ratio,{'A1':fp.D(50),'A2':denom}))
        self.assertEqual(evaluate_excel_subset(ratio,{'A1':fp.D(-10),'A2':fp.D(5)}),fp.D(-2))
        self.assertEqual(fp.ratio(field(-10),field(0),'x')['status'],'nonpositive_denominator')

    def test_excel_bindings_are_scalar_bounded_and_sheet_names_quoted(self):
        self.assertEqual(fp.excel_cell_reference('b2'),'B2')
        self.assertEqual(fp.excel_cell_reference('Inputs!$b$2'),"'Inputs'!$B$2")
        self.assertEqual(fp.excel_cell_reference({'sheet':"Owner's input",'cell':'$b2'}),"'Owner''s input'!$B2")
        self.assertEqual(fp.excel_cell_reference("'Owner''s input'!B2"),"'Owner''s input'!B2")
        self.assertEqual(fp.excel_cell_reference({'sheet':'原始输入','cell':'XFD1048576'}),"'原始输入'!XFD1048576")
        for invalid in ['=SUM(A1:A2)','A1:A2','A0','XFE1','A1048577','[other.xlsx]Input!A1',
                        "'x'!A1+1",{'sheet':'x/y','cell':'A1'},{'cell':'A1','formula':'1+1'},False]:
            with self.subTest(binding=invalid),self.assertRaises(ValueError):fp.excel_cell_reference(invalid)
        with self.assertRaises(ValueError):fp.excel_formulas({'revenue':'A1','reveneu':'A2'})
        with self.assertRaises(ValueError):fp.formula_catalog(['made_up_profit'])
        self.assertEqual(fp.excel_formulas({},['check.net_attribution'])['formulas']['check.net_attribution']['missing'],['attributable_profit','minority_profit','net_profit'])

    def test_standard_stock_codes_are_text_even_in_metadata_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'standard_利润表_合并报表.csv'
            p.write_text('股票代码,000651,000651\n股票名称,格力电器,格力电器\n日期,2024-12-31,2025-12-31\n财报类型,年报,年报\n货币,元,元\n营业收入,123,456\n',encoding='utf-8-sig')
            records=fp.import_csv(p)['records']
            self.assertTrue(all(r['stock_code']=='000651' for r in records))
            identifiers=[r for r in records if r['label']=='股票代码']
            self.assertTrue(all(r['status']=='identifier' and r['text']=='000651' and r['value'] is None for r in identifiers))
            self.assertEqual(next(r for r in records if r['label']=='营业收入')['value'],'123')

    def test_formula_cli_exports_and_preserves_bindings_file(self):
        with tempfile.TemporaryDirectory() as folder:
            bindings=Path(folder)/'bindings.json';output=Path(folder)/'formulas.json'
            bindings.write_text(json.dumps({'cfo':'A1','capex':'A2'}))
            with contextlib.redirect_stdout(io.StringIO()):
                code=fp.main(['formulas','--format','excel','--bindings',str(bindings),'--only','simplified_fcf','--output',str(output)])
            self.assertEqual(code,0)
            self.assertEqual(json.loads(output.read_text())['formulas']['simplified_fcf']['status'],'ready')
            original=bindings.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(fp.main(['formulas','--format','excel','--bindings',str(bindings),'--output',str(bindings)]),2)
                self.assertEqual(fp.main(['formulas','--format','excel']),2)
            self.assertEqual(bindings.read_bytes(),original)

    def test_fx_cashflow_is_money_while_ratios_stay_ratios(self):
        kind, unit, scale, _ = fp.row_unit("四、汇率变动对现金及现金等价物的影响", "元")
        self.assertEqual((kind, unit, scale), ("amount", "CNY", fp.D(1)))
        self.assertEqual(fp.row_unit("毛利率", "元")[:2], ("ratio", "ratio"))
        self.assertEqual(fp.row_unit("资产负债比率", "元")[:2], ("ratio", "ratio"))
        self.assertEqual(fp.row_unit("PE-TTM", "元")[:2], ("multiple", "multiple"))
        self.assertEqual(fp.row_unit("年度涨跌幅", "元")[:2], ("ratio", "ratio"))

    def test_strict_cli_distinguishes_written_results_from_passed_checks(self):
        import json
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder)/"facts.json"
            dst = Path(folder)/"checks.json"
            src.write_text(json.dumps({"schema_version":"financial-facts/v1","periods":[period("2025",assets=100,liabilities=40,equity=70)]}))
            with contextlib.redirect_stdout(io.StringIO()):
                code = fp.main(["calculate","--input",str(src),"--output",str(dst),"--strict"])
            self.assertEqual(code,2)
            self.assertEqual(json.loads(dst.read_text())["periods"][0]["checks"]["balance"]["status"],"unexplained_difference")
            src.write_text(json.dumps({"schema_version":"financial-facts/v1","periods":[]}))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fp.main(["calculate","--input",str(src),"--output",str(dst),"--strict"]),2)
                self.assertEqual(fp.main(["reconcile","--csv-facts",str(src),"--pdf-facts",str(src),"--output",str(dst),"--strict"]),2)

    def test_csv_identity_units_blank_zero_and_formula(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/"fixture利润表.csv"
            rows = [
                ["财报类型","股票代码","股票名称","年报","年报"],
                ["日期",'="000001"','="合并报表"',"2025-12-31","2024-12-31"],
                ["货币",'="000001"','="合并报表"',"元","元"],
                ["一、营业总收入(亿)",'="000001"','="合并报表"',"=2.0000","=1.0000"],
                ["利息收入(亿)",'="000001"','="合并报表"',"=0.0100","=0"],
                ["二、营业总成本(亿)",'="000001"','="合并报表"',"=1.5000","=1"],
                ["财务费用(亿)",'="000001"','="合并报表"',"=-0.0300",""],
                ["(其中)利息收入(亿)",'="000001"','="合并报表"',"=0.0400","--"],
                ["信用减值损失(亿)",'="000001"','="合并报表"',"=0.0050","=0.0000"],
                ["毛利率",'="000001"','="合并报表"',"=0.2981","29.81%"],
                ["员工人数(人)",'="000001"','="合并报表"',"=100","=90"],
                ["可疑输入(亿)",'="000001"','="合并报表"',"=SUM(A1:A2)",'="1+1"'],
                ["未注明单位",'="000001"','="合并报表"',"123","456"],
            ]
            with p.open("w",encoding="utf-8-sig",newline="") as f:
                csv.writer(f).writerows(rows)
            source = fp.import_csv(p)
            records = source["records"]
            self.assertEqual(records[0]["stock_code"],"000001")
            find = lambda row,col=4: next(r for r in records if r["source"]["row"]==row and r["source"]["column"]==col)
            self.assertEqual(find(4)["value"],"200000000.0000")
            self.assertEqual(find(4)["precision"],"10000.0000")
            self.assertEqual(find(5,5)["precision"],"10000.0000")
            self.assertIsNone(find(7,5)["value"])
            self.assertEqual(find(9,5)["value"],"0.0000")
            self.assertEqual(find(9,5)["precision"],"10000.0000")
            self.assertEqual(find(10)["unit"],"ratio")
            self.assertEqual(find(10,5)["value"],"0.2981")
            self.assertEqual(find(11)["unit"],"person")
            self.assertEqual(find(12)["status"],"formula_unexecuted")
            self.assertIsNone(find(12,5)["value"])
            self.assertEqual(find(13)["unit"],"CNY")
            self.assertEqual(find(13)["unit_basis"],"source_currency_metadata")
            self.assertEqual(fp.row_unit("未注明单位","")[0],"unknown")
            self.assertIn("财务费用(亿)",find(8)["source_path"])
            self.assertNotEqual(find(5)["source_path"],find(8)["source_path"])
            imported = {"sources":[source]}
            config = {"mappings":[{"field":"credit_impairment_impact","selector":{"row":9},"sign":-1,"reason":"CSV losses positive; PDF profit effect negative"}]}
            facts = fp.mapped_facts(imported,config)
            self.assertEqual(facts["periods"][0]["fields"]["credit_impairment_impact"]["value"],"-500000.0000")
            self.assertEqual(facts["periods"][0]["fields"]["credit_impairment_impact"]["source"]["row"],9)
            with self.assertRaises(ValueError):
                fp.mapped_facts(imported,{"mappings":[{"field":"something","selector":{"scope":"consolidated"}}]})
            find(13)["kind"] = "unknown"
            find(13)["unit"] = None
            with self.assertRaises(ValueError):
                fp.mapped_facts(imported,{"mappings":[{"field":"something","selector":{"row":13,"column":4}}]})

    def test_company_column_scope_and_yuan_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"example_资产负债表_合并报表.csv"
            p.write_text('财报类型,股票代码,股票名称,年报\n日期,600519,贵州茅台,2025-12-31\n货币,600519,贵州茅台,元\n一、资产总计,600519,贵州茅台,=303834844021\n应付职工薪酬,600519,贵州茅台,=500000000\n',encoding="utf-8-sig")
            records=fp.import_csv(p)["records"]
            asset=next(r for r in records if r["label"]=="一、资产总计")
            self.assertEqual(asset["scope"],"consolidated")
            self.assertEqual(asset["scope_basis"],"source_filename")
            self.assertEqual(asset["company_name"],"贵州茅台")
            self.assertEqual(asset["value"],"303834844021")
            self.assertEqual(asset["precision"],"1")
            self.assertEqual(asset["unit"],"CNY")
            self.assertEqual(next(r for r in records if r["label"]=="应付职工薪酬")["unit"],"CNY")
            self.assertEqual(fp.import_csv(p,scope_override="parent")["records"][0]["scope"],"parent")

    def test_standard_metadata_rows_keep_first_two_years(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"example_利润表_合并报表.csv"
            p.write_text('股票代码,="600519",="600519",="600519"\n股票名称,贵州茅台,贵州茅台,贵州茅台\n日期,2020-12-31,2021-12-31,2022-12-31\n财报类型,年报,年报,年报\n货币,元,元,元\n一、营业总收入,=100,=120,=150\n营业收入,=90,=110,=140\n',encoding="utf-8-sig")
            source=fp.import_csv(p)
            self.assertEqual(source["layout"],"standard-metadata-rows")
            self.assertEqual(source["data_start_column"],2)
            revenues=[r for r in source["records"] if r["label"]=="营业收入"]
            self.assertEqual([r["date"] for r in revenues],["2020-12-31","2021-12-31","2022-12-31"])
            self.assertEqual([r["source"]["column"] for r in revenues],[2,3,4])
            self.assertEqual([r["value"] for r in revenues],["90","110","140"])
            self.assertTrue(all(r["source"]["row"]==7 for r in revenues))
            self.assertEqual(revenues[0]["source"]["column_header_row"],4)
            self.assertEqual(revenues[0]["source"]["column_header"],"年报")

    def test_accounting_checks_and_missing_not_zero(self):
        p = period("2025",revenue=100,cost=30,surtax=10,selling=5,admin=4,rd=1,
            finance_expense=-2,finance_interest_income=3,
            financial_interest_income=2,financial_interest_expense=1,financial_fee_income=0,financial_fee_expense=0,
            investment_income=1,fair_value_gain=0,asset_disposal_gain=0,other_income=1,
            credit_impairment_impact=-1,asset_impairment_impact=-2,other_operating_impact=0,
            operating_profit=52,nonoperating_income=1,nonoperating_expense=2,profit_before_tax=51,
            income_tax=10,net_profit=41,attributable_profit=36,minority_profit=5,
            assets=100,liabilities=30,equity=70,cfo=50,cfi=-5,cff=-10,fx_effect=1,
            cash_change=36,cash_begin=10,cash_end=46,capex=6)
        result = fp.calculate({"schema_version":"financial-facts/v1","periods":[p]})
        out = result["periods"][0]
        self.assertEqual(fp.dec(out["derived"]["core_profit"]["value"]),fp.D(49))
        self.assertEqual(fp.dec(out["derived"]["simplified_fcf"]["value"]),fp.D(44))
        self.assertTrue(all(c["status"]=="pass" for c in out["checks"].values()))
        del p["fields"]["financial_fee_income"]
        p["fields"]["rd"]["value"] = None
        out = fp.calculate({"schema_version":"financial-facts/v1","periods":[p]})["periods"][0]
        self.assertEqual(out["derived"]["core_profit"]["status"],"missing_inputs")
        self.assertIn("rd",out["derived"]["core_profit"]["missing"])
        self.assertIn("financial_fee_income",out["checks"]["operating_profit"]["missing"])

    def test_tolerance_and_material_difference(self):
        p = period("2025",assets=100,liabilities=40,equity="60.01")
        a = fp.calc_period(p)["checks"]["balance"]
        self.assertEqual(a["status"],"pass")
        p["fields"]["equity"]["value"]="60.02"
        self.assertEqual(fp.calc_period(p)["checks"]["balance"]["status"],"unexplained_difference")

    def test_pdf_input_reconciliation_retains_values_and_missing(self):
        a=period("2025",revenue="100.0000",cost="30.0000",admin="4.0000")
        b=period("2025",revenue="100.004",cost="30.02")
        csv_data={"schema_version":"financial-facts/v1","periods":[a]}
        pdf_data={"schema_version":"financial-facts/v1","periods":[b]}
        results=fp.reconcile(csv_data,pdf_data)
        byfield={r["field"]:r for r in results["results"]}
        self.assertEqual(byfield["revenue"]["status"],"within_precision")
        self.assertEqual(byfield["cost"]["status"],"outside_precision")
        self.assertEqual(byfield["admin"]["status"],"missing_pdf")
        self.assertEqual(byfield["revenue"]["csv_value"],"100.0000")
        self.assertEqual(byfield["revenue"]["pdf_value"],"100.004")
        b["basis"]="restated"
        self.assertEqual(fp.reconcile(csv_data,pdf_data)["results"][0]["status"],"basis_requires_review")
        pairs={"pairs":[{"csv_period":"2025","pdf_period":"2025","fields":["revenue"],"allow_basis_difference":True,"basis_note":"Verified restatement did not affect this revenue line"}]}
        self.assertEqual(fp.reconcile(csv_data,pdf_data,pairs)["results"][0]["status"],"within_precision")

    def test_ytd_difference_and_comparison_guardrails(self):
        q3 = period("2026Q3","2026-01-01","2026-09-30","ytd",revenue=90,net_profit=20,assets=300,cash_end=40,cash_change=30)
        h1 = period("2026H1","2026-01-01","2026-06-30","ytd",revenue=60,net_profit=15,assets=280,cash_end=30,cash_change=20)
        old = period("2025Q3","2025-01-01","2025-09-30","ytd",revenue=0,net_profit=-2)
        data = {"schema_version":"financial-facts/v1","periods":[q3,h1,old],"differences":[{"later":"2026Q3","earlier":"2026H1","id":"2026singleQ3"}],"comparisons":[{"current":"2026Q3","base":"2025Q3"}]}
        result=fp.calculate(data)
        derived=result["derived_period_facts"][0]
        self.assertEqual(derived["start"],"2026-07-01")
        self.assertEqual(fp.dec(derived["fields"]["revenue"]["value"]),fp.D(30))
        self.assertNotIn("assets",derived["fields"])
        self.assertEqual(derived["fields"]["cash_begin"]["value"],"30")
        self.assertEqual(derived["fields"]["cash_end"]["value"],"40")
        self.assertEqual(result["comparisons"][0]["changes"]["revenue"]["status"],"nonpositive_base")
        self.assertIsNone(result["comparisons"][0]["changes"]["net_profit"]["growth"])
        h1["basis"]="restated-other"
        result=fp.calculate(data)
        self.assertEqual(result["differences"][0]["status"],"incomparable")
        self.assertFalse(result["derived_period_facts"])

    def test_parent_attribution_and_halfyear_difference(self):
        p=period("2025",net_profit=100,attributable_profit=0,minority_profit=0)
        p["scope"]="parent"
        self.assertEqual(fp.calc_period(p)["checks"]["net_attribution"]["status"],"not_applicable")
        annual=period("2026","2026-01-01","2026-12-31","annual",revenue=100)
        h1=period("2026H1","2026-01-01","2026-06-30","ytd",revenue=50)
        _,r=fp.difference_period(annual,h1,{"id":"H2"})
        self.assertEqual(r["status"],"incomparable")


if __name__=="__main__":
    unittest.main()

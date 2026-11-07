const labels = [
"界定公司业务与研究问题",
"对谁负责及兑现什么承诺",
"客户价值与盈利假说",
"把问题转换为证据地图",
"PDF 通读与资料定位",
"母子主体和利益归属",
"三表与附注交叉验证",
"资产战略与投入代价",
"利润来源与持续性",
"现金来源与支付能力",
"连接因果链并提出反证",
"形成有依据的经营判断",
"有限判断并明确缺口",
"交付结论及后续验证条件",
"原盈利假说需要改写"
];
export const title = "AI 如何理解并判断一家公司";
export const state = v => labels[v.phase];
export const colors = {"商业解释":"blue","寻找证据":"orange","财务互证":"teal","反证修正":"pink","结论":"purple","有限判断":"yellow"};
export const group = v => v.phase <= 2 ? "商业解释" : v.phase <= 5 ? "寻找证据" :
  v.phase <= 9 ? "财务互证" : (v.phase === 10 || v.phase === 14) ? "反证修正" :
  v.phase === 12 ? "有限判断" : "结论";
export const step = action => ({
DefineScope:"确认业务与实际披露期间",
MapResponsiblePeople:"区分付款 使用 决策和受益",
ProposeEarningMechanism:"解释为何付钱和利润如何保留",
MapRequiredEvidence:"按行业设计章节与旁证计划",
LocateReadAndCrossReference:"通读并追踪附注或复用已有证据",
SourcesUnavailable:"替代来源仍缺关键材料",
MapGroupAndOwnership:"定位经营 现金 债务与权益主体",
ChooseAssets:"检验投入怎样支持交付",
JudgeAssets:"形成战略与资产质量判断",
ChooseEarnings:"检验利润怎样形成",
JudgeEarnings:"形成利润质量判断",
ChooseCash:"检验收现与可用性",
JudgeCash:"形成现金质量判断",
ReopenEvidenceFromStatement:"发现异常 回到证据地图",
JoinCausalEvidence:"三表分别核查后汇合",
AssessAlternatives:"核查支持 反证和缺失",
SupportedJudgment:"关键证据足以形成判断",
LimitedJudgment:"关键环节未证实",
SeekMoreEvidence:"扩词 换章节 查跨年或外部旁证",
ChallengeMechanism:"事实反驳原解释",
ReframeWhoBenefits:"重新审视责任对象与盈利机制",
DeliverJudgment:"交付判断 风险与失效条件",
NewDisclosure:"新披露或客户行为改变",
WaitForNextResearch:"等待下一轮证据"
})[action] ?? action;

export const title = "分析一家公司的六步认知流程";
export const state = v =>
  v.phase <= 1 ? "1 明确责任对象" :
  v.phase === 2 ? "2 提出盈利假说" :
  v.phase <= 5 ? "3 建立并补齐证据" :
  v.phase <= 9 ? "4 业务与三表互证" :
  (v.phase === 10 || v.phase === 14) ? "5 检验反证并修正" :
  "6 交付结论与监测";

export const step = action => ({
  DefineScope: "确认公司业务与期间",
  MapResponsiblePeople: "区分付款 使用 决策和受益",
  ProposeEarningMechanism: "追问支持与反证",
  MapRequiredEvidence: "设计证据地图",
  LocateReadAndCrossReference: "通读原件或复用核准读稿",
  SourcesUnavailable: "关键材料仍缺失 形成有限判断",
  MapGroupAndOwnership: "穿透主体与利益归属",
  ChooseAssets: "检验投入怎样支持交付",
  JudgeAssets: "解释资产战略",
  ChooseEarnings: "检验利润怎样形成",
  JudgeEarnings: "解释利润质量",
  ChooseCash: "检验收现与可用性",
  JudgeCash: "解释现金质量",
  ReopenEvidenceFromStatement: "发现异常 补查原件",
  JoinCausalEvidence: "连接投入 利润与现金",
  AssessAlternatives: "比较支持 反证和缺口",
  SupportedJudgment: "交付有据判断或有限判断",
  LimitedJudgment: "交付有据判断或有限判断",
  SeekMoreEvidence: "关键环节缺证或冲突 补充调查",
  ChallengeMechanism: "原机制被事实反驳",
  ReframeWhoBenefits: "重新审视责任对象与盈利机制",
  DeliverJudgment: "交付证据 风险与失效条件",
  NewDisclosure: "新披露或客户行为改变",
  WaitForNextResearch: "等待新证据"
})[action] ?? action;

export const title = "新公司怎样发现三表之外的业务证据";
const names = {
  Map: "按业务问题建立公司与行业词图",
  Cache: "查询SQLite定位既有证据",
  Source: "获取并核验原始PDF",
  Fallback: "改查公司或交易所原始披露",
  Read: "通读业务治理重大事项与重要附注",
  Locate: "按语义主体及同义词定位",
  Expand: "扩展目录邻页OCR与其他年度 限一轮",
  Verify: "核对原页附注口径与跨年变化",
  External: "补客户监管等原始旁证",
  Assess: "比较盈利解释履责证据与替代解释",
  Enough: "形成有依据的业务与履责判断",
  Limited: "形成有限判断并记录证据缺口"
};
export const state = v => names[v.phase];
const steps = {
  BuildQuestionMap: "明确谁付款使用受益及承诺",
  ReuseVerifiedArchive: "复用已核验原件 [缓存命中]",
  NeedOriginalReport: "取得对应期间原件 [未命中或版本失效]",
  VerifyPrimaryReport: "核对主体期间版本与来源",
  PrimarySourceFails: "保留失败并更换原始来源",
  VerifyAlternateReport: "核验替代来源原件",
  NoVerifiedReport: "记录缺失期间和影响 [无可核验原件]",
  ReadCoreChapters: "先理解业务及非三表事项",
  LocateRelevantEvidence: "找到候选证据",
  NoHitNeedsExpansion: "未命中只表示尚未定位",
  ExpandFindsEvidence: "扩搜找到相关章节",
  ExpansionRemainsUnresolved: "保留检索范围和缺口 [仍未定位]",
  OriginalPagesPermitEvaluation: "核准交付效果并辨明证据边界",
  OriginalPagesNeedOutsideEvidence: "实际效果须从客户或监管补证",
  CustomerEffectUnevaluated: "核准交付效果并辨明证据边界",
  ContradictionNeedsExpansion: "重查语义主体与年度 [本轮尚未扩搜]",
  OutsideEvidencePermitsEvaluation: "核对旁证质量与适用边界",
  OutsideEvidenceUnresolved: "核对旁证质量与适用边界",
  SupportedJudgment: "连接事实推断及失效条件",
  BoundedJudgment: "写明缺口及可能改变判断的证据",
  Done: "结束本轮证据调查"
};
export const step = action => steps[action] ?? action;
export const group = v => {
  if (["Map", "Cache"].includes(v.phase)) return "问题定义";
  if (["Source", "Fallback", "Read"].includes(v.phase)) return "原件与阅读";
  if (["Locate", "Expand", "Verify", "External"].includes(v.phase)) return "证据调查";
  if (v.phase === "Assess") return "解释评估";
  return v.phase === "Enough" ? "判断有据" : "明确缺口";
};
export const colors = {"问题定义":"blue","原件与阅读":"purple","证据调查":"teal","解释评估":"orange","判断有据":"teal","明确缺口":"yellow"};

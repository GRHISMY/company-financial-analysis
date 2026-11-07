const phases = [
  "界定公司、期间与问题", "查旧研究与资料覆盖", "补齐原件、归档并提取",
  "读 PDF 与核验数字并行", "形成有来源的研究发现", "综合解释、反证与缺口",
  "复核并形成当前报告", "交付本轮结论，等待新证据"
];
const level = p => p >= 5 ? 2 : p >= 4 ? 1 : 0;
const ready = v => Array.isArray(v.indexed) ? false :
  typeof v.indexed === "object" && v.indexed !== null &&
  v.indexed[1] === v.generation && v.indexed[2] === v.covered && v.indexed[3] === v.extracted && v.indexed[4] === level(v.phase);
export const title = "单公司研究工作流和并行的 SQLite";
export const state = v => ({
  "研究推进（Skill 约定，AI／人工执行）": phases[v.phase],
  "SQLite 检索缓存（CLI）": v.indexed[1] === -1 ? "没有可用索引" :
    ready(v) ? "当前版本可检索" : "依赖已变，索引待同步"
});
export const step = action => ({
  DefineScope: "确定本轮研究范围",
  NeedMaterials: "发现范围缺口或正文未提取",
  ReuseMaterials: "找到可复用的同版本资料",
  ArchiveOriginals: "归档现有原件",
  ExtractOriginals: "提取正文并保留页码",
  Investigate: "AI 读稿、看原页、核准采用值",
  RecordFindings: "登记事实、计算与推断",
  ReviewInterpretation: "AI／人工评估充分性，保留有限结论",
  RegisterAndDeliver: "登记真实依赖并交付",
  NewDisclosure: "新披露，重新界定覆盖",
  SyncIndex: "同步原文和已有研究记录",
  SearchAndExpand: "检索并展开原文",
  Done: "等待下一轮研究"
})[action] ?? action;

export const colors = {"准备": "blue", "研究": "teal", "复核": "orange", "交付": "purple"};
export const group = v => v.phase <= 2 ? "准备" : v.phase <= 4 ? "研究" : v.phase <= 6 ? "复核" : "交付";

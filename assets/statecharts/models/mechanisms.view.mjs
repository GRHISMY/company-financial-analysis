const reading = v => v.epoch >= 0 && v.epoch !== v.version ? "旧回执需显式重置" :
  v.ack === v.version ? "当前文本块已确认" :
  v.pending === v.version ? "文本已投递，待确认" : "当前回执尚未开始";
const artifact = v => v.adopted !== v.version ?
  (v.adopted < 0 ? "采用值尚未登记" : "已绑定上游过期，需复核") :
  v.report === v.version ? "依赖当前，报告已登记" :
  v.report < 0 ? "采用值当前，报告待登记" : "采用值已重核，报告待重登记";
const cache = v => v.captured >= 0 ?
  (v.captured === v.version ? "同步进行中" : "输入已变，同步需回滚") :
  v.indexed < 0 ? "尚未入库" :
  v.indexed === v.version ? "当前可检索" : "索引过期，查询被阻断";
export const title = "CLI 已实现的并行状态";
export const state = v => ({
  "PDF 文本投递回执": reading(v),
  "显式登记的采用值到报告依赖链": artifact(v),
  "SQLite 检索缓存": cache(v)
});
export const step = action => ({
  Deliver: "投递当前文本块",
  RepeatPending: "重放尚未确认的同一块",
  AcceptMatchingAck: "用匹配的块 ID 和摘要确认",
  ResetOldReceipt: "显式重置旧回执，保留历史",
  RegisterAdopted: "复核后登记采用值",
  RegisterDependentReport: "登记报告及实际上游",
  CaptureIndex: "捕获当前依赖快照",
  CommitIndex: "提交未变化的快照",
  AbortChangedCapture: "检测变化并回滚",
  ChangeBoundInput: "已绑定的输入版本变化",
  QueryCurrent: "返回当前命中，阅读状态不变",
  QueryUnavailable: "返回缺失或过期，阅读状态不变"
})[action] ?? action;

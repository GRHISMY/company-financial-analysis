-------------------- MODULE CompanyReasoning --------------------
EXTENDS Naturals, FiniteSets
VARIABLES phase, round, roles, entities, hypothesis, plan, pdfRead, checked, evidence, confidence, gaps
vars == <<phase, round, roles, entities, hypothesis, plan, pdfRead, checked, evidence, confidence, gaps>>
Areas == {"assets","earnings","cash"}
TypeOK ==
  /\ phase \in 0..14 /\ round \in 0..1
  /\ roles \in {"unknown","known","gaps"} /\ entities \in {"unknown","known","gaps"}
  /\ hypothesis \in BOOLEAN /\ plan \in BOOLEAN /\ pdfRead \in BOOLEAN
  /\ checked \in SUBSET Areas
  /\ evidence \in {"unknown","supports","refutes","missing"}
  /\ confidence \in {"none","supported","limited"} /\ gaps \in BOOLEAN
Init ==
  /\ phase = 0 /\ round = 0 /\ roles = "unknown" /\ entities = "unknown"
  /\ hypothesis = FALSE /\ plan = FALSE /\ pdfRead = FALSE /\ checked = {}
  /\ evidence = "unknown" /\ confidence = "none" /\ gaps = FALSE
DefineScope ==
  /\ phase = 0 /\ phase' = 1
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
MapResponsiblePeople ==
  /\ phase = 1 /\ phase' = 2 /\ roles' \in {"known","gaps"}
  /\ UNCHANGED <<round,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
ProposeEarningMechanism ==
  /\ phase = 2 /\ phase' = 3 /\ hypothesis' = TRUE
  /\ UNCHANGED <<round,roles,entities,plan,pdfRead,checked,evidence,confidence,gaps>>
MapRequiredEvidence ==
  /\ phase = 3 /\ phase' = 4 /\ plan' = TRUE
  /\ pdfRead' = FALSE /\ checked' = {} /\ evidence' = "unknown"
  /\ confidence' = "none" /\ gaps' = FALSE /\ entities' = "unknown"
  /\ UNCHANGED <<round,roles,hypothesis>>
LocateReadAndCrossReference ==
  /\ phase = 4 /\ phase' = 5 /\ pdfRead' = TRUE
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,checked,evidence,confidence,gaps>>
SourcesUnavailable ==
  /\ phase = 4 /\ phase' = 12 /\ confidence' = "limited" /\ gaps' = TRUE
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence>>
MapGroupAndOwnership ==
  /\ phase = 5 /\ phase' = 6 /\ entities' \in {"known","gaps"}
  /\ UNCHANGED <<round,roles,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
ChooseAssets ==
  /\ phase = 6 /\ "assets" \notin checked /\ phase' = 7
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
JudgeAssets ==
  /\ phase = 7 /\ phase' = 6 /\ checked' = checked \cup {"assets"}
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,evidence,confidence,gaps>>
ChooseEarnings ==
  /\ phase = 6 /\ "earnings" \notin checked /\ phase' = 8
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
JudgeEarnings ==
  /\ phase = 8 /\ phase' = 6 /\ checked' = checked \cup {"earnings"}
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,evidence,confidence,gaps>>
ChooseCash ==
  /\ phase = 6 /\ "cash" \notin checked /\ phase' = 9
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
JudgeCash ==
  /\ phase = 9 /\ phase' = 6 /\ checked' = checked \cup {"cash"}
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,evidence,confidence,gaps>>
ReopenEvidenceFromStatement ==
  /\ phase \in {7,8,9} /\ round = 0 /\ round' = 1 /\ phase' = 3
  /\ UNCHANGED <<roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
JoinCausalEvidence ==
  /\ phase = 6 /\ checked = Areas /\ phase' = 10
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
AssessAlternatives ==
  /\ phase = 10 /\ evidence = "unknown" /\ evidence' \in {"supports","refutes","missing"}
  /\ UNCHANGED <<phase,round,roles,entities,hypothesis,plan,pdfRead,checked,confidence,gaps>>
SupportedJudgment ==
  /\ phase = 10 /\ evidence \in {"supports","refutes"} /\ roles = "known" /\ entities = "known"
  /\ pdfRead /\ checked = Areas /\ phase' = 11 /\ confidence' = "supported" /\ gaps' = FALSE
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence>>
LimitedJudgment ==
  /\ phase = 10 /\ evidence # "unknown"
  /\ (evidence = "missing" \/ roles = "gaps" \/ entities = "gaps") /\ phase' = 12
  /\ confidence' = "limited" /\ gaps' = TRUE
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence>>
SeekMoreEvidence ==
  /\ phase = 10 /\ evidence \in {"refutes","missing"} /\ round = 0
  /\ round' = 1 /\ phase' = 3
  /\ UNCHANGED <<roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
ChallengeMechanism ==
  /\ phase = 10 /\ evidence = "refutes" /\ round = 0
  /\ round' = 1 /\ phase' = 14
  /\ UNCHANGED <<roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
ReframeWhoBenefits ==
  /\ phase = 14 /\ phase' = 1 /\ roles' = "unknown" /\ entities' = "unknown"
  /\ hypothesis' = FALSE /\ plan' = FALSE /\ pdfRead' = FALSE /\ checked' = {}
  /\ evidence' = "unknown" /\ confidence' = "none" /\ gaps' = FALSE
  /\ UNCHANGED round
DeliverJudgment ==
  /\ phase \in {11,12} /\ phase' = 13
  /\ UNCHANGED <<round,roles,entities,hypothesis,plan,pdfRead,checked,evidence,confidence,gaps>>
NewDisclosure ==
  /\ phase = 13 /\ round = 0 /\ round' = 1 /\ phase' = 3
  /\ plan' = FALSE /\ pdfRead' = FALSE /\ checked' = {} /\ evidence' = "unknown"
  /\ confidence' = "none" /\ gaps' = FALSE /\ entities' = "unknown"
  /\ UNCHANGED <<roles,hypothesis>>
WaitForNextResearch ==
  /\ phase = 13 /\ UNCHANGED vars
Next == DefineScope \/ MapResponsiblePeople \/ ProposeEarningMechanism \/ MapRequiredEvidence \/
  LocateReadAndCrossReference \/ SourcesUnavailable \/ MapGroupAndOwnership \/
  ChooseAssets \/ JudgeAssets \/ ChooseEarnings \/ JudgeEarnings \/ ChooseCash \/ JudgeCash \/
  ReopenEvidenceFromStatement \/ JoinCausalEvidence \/ AssessAlternatives \/ SupportedJudgment \/ LimitedJudgment \/
  SeekMoreEvidence \/ ChallengeMechanism \/ ReframeWhoBenefits \/ DeliverJudgment \/
  NewDisclosure \/ WaitForNextResearch
Spec == Init /\ [][Next]_vars
ConclusionsDescribeWhoAndWhy ==
  confidence # "none" => roles # "unknown" /\ hypothesis /\ plan
SupportedJudgmentHasBusinessAndEntityFrame ==
  confidence = "supported" => roles = "known" /\ entities = "known" /\ pdfRead
SupportedJudgmentUsesThreeStatements ==
  confidence = "supported" => checked = Areas /\ evidence \in {"supports","refutes"}
MissingEvidenceNeverBecomesFullConfidence ==
  evidence \in {"unknown","missing"} => confidence # "supported"
LimitedConclusionNamesGaps == confidence = "limited" => gaps
===================================================================

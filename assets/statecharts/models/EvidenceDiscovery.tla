------------------------- MODULE EvidenceDiscovery -------------------------
EXTENDS Naturals, TLC

\* benefitEvaluated means enough evidence to assess customer effects and promise
\* fulfillment, whether the resulting judgment is positive or negative.
VARIABLES phase, source, pdfVerified, coreRead, pageChecked,
          expanded, searchResult, external, benefitEvaluated, gap

vars == <<phase, source, pdfVerified, coreRead, pageChecked,
          expanded, searchResult, external, benefitEvaluated, gap>>

Phases == {"Map", "Cache", "Source", "Fallback", "Read", "Locate",
           "Expand", "Verify", "External", "Assess", "Enough", "Limited"}

Init ==
  /\ phase = "Map"
  /\ source = "none"
  /\ pdfVerified = FALSE
  /\ coreRead = FALSE
  /\ pageChecked = FALSE
  /\ expanded = FALSE
  /\ searchResult = "unknown"
  /\ external = "notNeeded"
  /\ benefitEvaluated = FALSE
  /\ gap = "none"

BuildQuestionMap ==
  /\ phase = "Map"
  /\ phase' = "Cache"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked, expanded,
                  searchResult, external, benefitEvaluated, gap>>

ReuseVerifiedArchive ==
  /\ phase = "Cache"
  /\ phase' = "Read"
  /\ source' = "cache"
  /\ pdfVerified' = TRUE
  /\ UNCHANGED <<coreRead, pageChecked, expanded, searchResult,
                  external, benefitEvaluated, gap>>

NeedOriginalReport ==
  /\ phase = "Cache"
  /\ phase' = "Source"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked, expanded,
                  searchResult, external, benefitEvaluated, gap>>

VerifyPrimaryReport ==
  /\ phase = "Source"
  /\ phase' = "Read"
  /\ source' = "primary"
  /\ pdfVerified' = TRUE
  /\ UNCHANGED <<coreRead, pageChecked, expanded, searchResult,
                  external, benefitEvaluated, gap>>

PrimarySourceFails ==
  /\ phase = "Source"
  /\ phase' = "Fallback"
  /\ source' = "failed"
  /\ UNCHANGED <<pdfVerified, coreRead, pageChecked, expanded,
                  searchResult, external, benefitEvaluated, gap>>

VerifyAlternateReport ==
  /\ phase = "Fallback"
  /\ phase' = "Read"
  /\ source' = "alternate"
  /\ pdfVerified' = TRUE
  /\ UNCHANGED <<coreRead, pageChecked, expanded, searchResult,
                  external, benefitEvaluated, gap>>

NoVerifiedReport ==
  /\ phase = "Fallback"
  /\ phase' = "Limited"
  /\ source' = "unavailable"
  /\ gap' = "source"
  /\ UNCHANGED <<pdfVerified, coreRead, pageChecked, expanded,
                  searchResult, external, benefitEvaluated>>

ReadCoreChapters ==
  /\ phase = "Read"
  /\ pdfVerified
  /\ phase' = "Locate"
  /\ coreRead' = TRUE
  /\ UNCHANGED <<source, pdfVerified, pageChecked, expanded,
                  searchResult, external, benefitEvaluated, gap>>

LocateRelevantEvidence ==
  /\ phase = "Locate"
  /\ phase' = "Verify"
  /\ searchResult' = "found"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked, expanded,
                  external, benefitEvaluated, gap>>

NoHitNeedsExpansion ==
  /\ phase = "Locate"
  /\ phase' = "Expand"
  /\ searchResult' = "unresolved"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked, expanded,
                  external, benefitEvaluated, gap>>

ExpandFindsEvidence ==
  /\ phase = "Expand"
  /\ ~expanded
  /\ phase' = "Verify"
  /\ expanded' = TRUE
  /\ searchResult' = "found"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked,
                  external, benefitEvaluated, gap>>

ExpansionRemainsUnresolved ==
  /\ phase = "Expand"
  /\ ~expanded
  /\ phase' = "Assess"
  /\ expanded' = TRUE
  /\ searchResult' = "unresolved"
  /\ pageChecked' = FALSE
  /\ gap' = "search"
  /\ UNCHANGED <<source, pdfVerified, coreRead,
                  external, benefitEvaluated>>

OriginalPagesPermitEvaluation ==
  /\ phase = "Verify"
  /\ phase' = "Assess"
  /\ pageChecked' = TRUE
  /\ benefitEvaluated' = TRUE
  /\ gap' = "none"
  /\ UNCHANGED <<source, pdfVerified, coreRead, expanded,
                  searchResult, external>>

OriginalPagesNeedOutsideEvidence ==
  /\ phase = "Verify"
  /\ phase' = "External"
  /\ pageChecked' = TRUE
  /\ external' = "needed"
  /\ UNCHANGED <<source, pdfVerified, coreRead, expanded,
                  searchResult, benefitEvaluated, gap>>

CustomerEffectUnevaluated ==
  /\ phase = "Verify"
  /\ phase' = "Assess"
  /\ pageChecked' = TRUE
  /\ benefitEvaluated' = FALSE
  /\ gap' = "benefit"
  /\ UNCHANGED <<source, pdfVerified, coreRead, expanded,
                  searchResult, external>>

ContradictionNeedsExpansion ==
  /\ phase = "Verify"
  /\ ~expanded
  /\ phase' = "Expand"
  /\ pageChecked' = TRUE
  /\ gap' = "contradiction"
  /\ UNCHANGED <<source, pdfVerified, coreRead, expanded,
                  searchResult, external, benefitEvaluated>>

OutsideEvidencePermitsEvaluation ==
  /\ phase = "External"
  /\ phase' = "Assess"
  /\ external' = "verified"
  /\ benefitEvaluated' = TRUE
  /\ gap' = "none"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked,
                  expanded, searchResult>>

OutsideEvidenceUnresolved ==
  /\ phase = "External"
  /\ phase' = "Assess"
  /\ external' = "missing"
  /\ benefitEvaluated' = FALSE
  /\ gap' = "outside"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked,
                  expanded, searchResult>>

SupportedJudgment ==
  /\ phase = "Assess"
  /\ pdfVerified /\ coreRead /\ pageChecked
  /\ benefitEvaluated
  /\ external \in {"notNeeded", "verified"}
  /\ gap = "none"
  /\ phase' = "Enough"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked, expanded,
                  searchResult, external, benefitEvaluated, gap>>

BoundedJudgment ==
  /\ phase = "Assess"
  /\ gap # "none"
  /\ phase' = "Limited"
  /\ UNCHANGED <<source, pdfVerified, coreRead, pageChecked, expanded,
                  searchResult, external, benefitEvaluated, gap>>

Done ==
  /\ phase \in {"Enough", "Limited"}
  /\ UNCHANGED vars

Next == BuildQuestionMap \/ ReuseVerifiedArchive \/ NeedOriginalReport
     \/ VerifyPrimaryReport \/ PrimarySourceFails \/ VerifyAlternateReport
     \/ NoVerifiedReport \/ ReadCoreChapters \/ LocateRelevantEvidence
     \/ NoHitNeedsExpansion \/ ExpandFindsEvidence \/ ExpansionRemainsUnresolved
     \/ OriginalPagesPermitEvaluation \/ OriginalPagesNeedOutsideEvidence
     \/ CustomerEffectUnevaluated \/ ContradictionNeedsExpansion
     \/ OutsideEvidencePermitsEvaluation \/ OutsideEvidenceUnresolved
     \/ SupportedJudgment \/ BoundedJudgment \/ Done

TypeOK ==
  /\ phase \in Phases
  /\ source \in {"none", "cache", "primary", "failed", "alternate", "unavailable"}
  /\ pdfVerified \in BOOLEAN /\ coreRead \in BOOLEAN /\ pageChecked \in BOOLEAN
  /\ expanded \in BOOLEAN /\ benefitEvaluated \in BOOLEAN
  /\ searchResult \in {"unknown", "found", "unresolved", "notDisclosed"}
  /\ external \in {"notNeeded", "needed", "verified", "missing"}
  /\ gap \in {"none", "source", "search", "benefit", "contradiction", "outside"}

CacheNeverReplacesReading ==
  phase \in {"Locate", "Expand", "Verify", "External", "Assess", "Enough"}
  => pdfVerified /\ coreRead

NoHitIsNotNondisclosure == searchResult # "notDisclosed"

FailureUsesAlternative == (source = "failed") => (phase = "Fallback")

SupportedMeansVerified ==
  (phase = "Enough") =>
    pdfVerified /\ coreRead /\ pageChecked /\ searchResult = "found"
    /\ benefitEvaluated /\ external \in {"notNeeded", "verified"} /\ gap = "none"

UnevaluatedEffectLimitsConclusion ==
  (phase \in {"Enough", "Limited"} /\ ~benefitEvaluated) => phase = "Limited"

GapsAreExplicit == (phase = "Limited") => gap # "none"

=============================================================================

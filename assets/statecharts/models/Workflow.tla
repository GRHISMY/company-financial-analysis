-------------------- MODULE Workflow --------------------
EXTENDS Naturals, Integers, Sequences
VARIABLES phase, generation, originals, covered, extracted, reviewed, indexed
vars == <<phase, generation, originals, covered, extracted, reviewed, indexed>>
Levels == 0..2
Stamp == <<generation, covered, extracted, IF phase >= 5 THEN 2 ELSE IF phase >= 4 THEN 1 ELSE 0>>
TypeOK ==
  /\ phase \in 0..7 /\ generation \in 0..1
  /\ originals \in BOOLEAN /\ covered \in BOOLEAN /\ extracted \in BOOLEAN /\ reviewed \in BOOLEAN
  /\ indexed \in {<<-1,FALSE,FALSE,-1>>} \cup ({0,1} \X BOOLEAN \X BOOLEAN \X Levels)
Init ==
  /\ phase = 0 /\ generation = 0 /\ originals \in BOOLEAN
  /\ covered \in BOOLEAN /\ (covered => originals)
  /\ extracted \in BOOLEAN /\ (extracted => originals) /\ reviewed = FALSE
  /\ indexed \in IF originals THEN {<<-1,FALSE,FALSE,-1>>,Stamp,<<0,FALSE,FALSE,0>>} ELSE {<<-1,FALSE,FALSE,-1>>}
DefineScope ==
  /\ phase = 0 /\ phase' = 1
  /\ UNCHANGED <<generation, originals, covered, extracted, reviewed, indexed>>
NeedMaterials ==
  /\ phase = 1 /\ (~covered \/ ~extracted) /\ phase' = 2
  /\ UNCHANGED <<generation, originals, covered, extracted, reviewed, indexed>>
ReuseMaterials ==
  /\ phase = 1 /\ originals /\ covered /\ extracted /\ phase' = 3
  /\ UNCHANGED <<generation, originals, covered, extracted, reviewed, indexed>>
ArchiveOriginals ==
  /\ phase = 2 /\ ~covered
  /\ originals' = TRUE /\ covered' = TRUE /\ extracted' = FALSE
  /\ UNCHANGED <<phase, generation, reviewed, indexed>>
ExtractOriginals ==
  /\ phase = 2 /\ originals /\ covered /\ ~extracted
  /\ phase' = 3 /\ extracted' = TRUE
  /\ UNCHANGED <<generation, originals, covered, reviewed, indexed>>
Investigate ==
  /\ phase = 3 /\ extracted /\ phase' = 4
  /\ UNCHANGED <<generation, originals, covered, extracted, reviewed, indexed>>
RecordFindings ==
  /\ phase = 4 /\ phase' = 5
  /\ UNCHANGED <<generation, originals, covered, extracted, reviewed, indexed>>
ReviewInterpretation ==
  /\ phase = 5 /\ phase' = 6 /\ reviewed' = TRUE
  /\ UNCHANGED <<generation, originals, covered, extracted, indexed>>
RegisterAndDeliver ==
  /\ phase = 6 /\ reviewed /\ phase' = 7
  /\ UNCHANGED <<generation, originals, covered, extracted, reviewed, indexed>>
NewDisclosure ==
  /\ phase = 7 /\ generation = 0
  /\ generation' = 1 /\ phase' = 1
  /\ originals' = TRUE /\ covered' = FALSE /\ extracted' = FALSE /\ reviewed' = FALSE
  /\ UNCHANGED indexed
SyncIndex ==
  /\ originals /\ indexed # Stamp /\ indexed' = Stamp
  /\ UNCHANGED <<phase, generation, originals, covered, extracted, reviewed>>
SearchAndExpand ==
  /\ indexed = Stamp /\ UNCHANGED vars
Done ==
  /\ phase = 7 /\ generation = 1 /\ UNCHANGED vars
Next == DefineScope \/ NeedMaterials \/ ReuseMaterials \/ ArchiveOriginals \/
        ExtractOriginals \/ Investigate \/ RecordFindings \/ ReviewInterpretation \/
        RegisterAndDeliver \/ NewDisclosure \/ SyncIndex \/ SearchAndExpand \/ Done
Spec == Init /\ [][Next]_vars
WorkUsesArchivedMaterial == phase >= 3 => originals /\ covered /\ extracted
DeliveryIncludesResearchReview == phase = 7 => reviewed
CurrentIndexHasArchivedMaterial == indexed = Stamp => originals
=========================================================

-------------------- MODULE Mechanisms --------------------
EXTENDS Integers, Sequences
VARIABLES version, epoch, pending, ack, adopted, report, indexed, captured,
          event, ackBefore, returned, committed, acceptedPending
vars == <<version, epoch, pending, ack, adopted, report, indexed, captured,
          event, ackBefore, returned, committed, acceptedPending>>
Versions == {-1,0,1}
TypeOK ==
  /\ version \in {0,1}
  /\ epoch \in Versions /\ pending \in Versions /\ ack \in Versions
  /\ adopted \in Versions /\ report \in Versions
  /\ indexed \in Versions /\ captured \in Versions
  /\ event \in {"other","query","blocked","commit","ack"}
  /\ ackBefore \in Versions /\ returned \in Versions
  /\ committed \in Versions /\ acceptedPending \in Versions
Init ==
  /\ version = 0
  /\ epoch = -1 /\ pending = -1 /\ ack = -1
  /\ adopted = -1 /\ report = -1 /\ indexed = -1 /\ captured = -1
  /\ event = "other" /\ ackBefore = -1 /\ returned = -1
  /\ committed = -1 /\ acceptedPending = -1
Other == /\ event' = "other"
         /\ ackBefore' = -1 /\ returned' = -1 /\ committed' = -1 /\ acceptedPending' = -1
Deliver ==
  /\ ack # version /\ pending = -1 /\ epoch \in {-1,version}
  /\ epoch' = version /\ pending' = version /\ Other
  /\ UNCHANGED <<version, ack, adopted, report, indexed, captured>>
RepeatPending ==
  /\ pending = version /\ epoch = version /\ Other
  /\ UNCHANGED <<version, epoch, pending, ack, adopted, report, indexed, captured>>
AcceptMatchingAck ==
  /\ epoch = version /\ pending = version
  /\ ack' = version /\ pending' = -1
  /\ event' = "ack" /\ acceptedPending' = pending
  /\ ackBefore' = -1 /\ returned' = -1 /\ committed' = -1
  /\ UNCHANGED <<version, epoch, adopted, report, indexed, captured>>
ResetOldReceipt ==
  /\ epoch >= 0 /\ epoch # version
  /\ epoch' = version /\ pending' = -1 /\ ack' = -1 /\ Other
  /\ UNCHANGED <<version, adopted, report, indexed, captured>>
RegisterAdopted ==
  /\ adopted # version /\ adopted' = version /\ Other
  /\ UNCHANGED <<version, epoch, pending, ack, report, indexed, captured>>
RegisterDependentReport ==
  /\ adopted = version /\ report # version /\ report' = version /\ Other
  /\ UNCHANGED <<version, epoch, pending, ack, adopted, indexed, captured>>
CaptureIndex ==
  /\ captured = -1 /\ indexed # version /\ captured' = version /\ Other
  /\ UNCHANGED <<version, epoch, pending, ack, adopted, report, indexed>>
CommitIndex ==
  /\ captured = version /\ indexed' = captured /\ captured' = -1
  /\ event' = "commit" /\ committed' = captured
  /\ ackBefore' = -1 /\ returned' = -1 /\ acceptedPending' = -1
  /\ UNCHANGED <<version, epoch, pending, ack, adopted, report>>
AbortChangedCapture ==
  /\ captured >= 0 /\ captured # version /\ captured' = -1 /\ Other
  /\ UNCHANGED <<version, epoch, pending, ack, adopted, report, indexed>>
ChangeBoundInput ==
  /\ version = 0 /\ ack = 0 /\ adopted = 0 /\ report = 0
  /\ (indexed = 0 \/ captured = 0)
  /\ version' = 1 /\ Other
  /\ UNCHANGED <<epoch, pending, ack, adopted, report, indexed, captured>>
QueryCurrent ==
  /\ indexed = version
  /\ event' = "query" /\ ackBefore' = ack /\ returned' = indexed
  /\ committed' = -1 /\ acceptedPending' = -1
  /\ UNCHANGED <<version, epoch, pending, ack, adopted, report, indexed, captured>>
QueryUnavailable ==
  /\ indexed # version
  /\ event' = "blocked" /\ ackBefore' = ack /\ returned' = -1
  /\ committed' = -1 /\ acceptedPending' = -1
  /\ UNCHANGED <<version, epoch, pending, ack, adopted, report, indexed, captured>>
Next == Deliver \/ RepeatPending \/ AcceptMatchingAck \/ ResetOldReceipt \/
        RegisterAdopted \/ RegisterDependentReport \/ CaptureIndex \/ CommitIndex \/
        AbortChangedCapture \/ ChangeBoundInput \/ QueryCurrent \/ QueryUnavailable
Spec == Init /\ [][Next]_vars
AckRequiresMatchingCurrentPending == event = "ack" => acceptedPending = version /\ ack = version
CurrentReportHasCurrentDeclaredUpstream == report = version => adopted = version
QueryDoesNotConfirmReading == event \in {"query","blocked"} => ack = ackBefore
ReturnedHitsAreCurrent == event = "query" => returned = version /\ indexed = version
UnavailableIndexReturnsNoHits == event = "blocked" => returned = -1
CommitUsesUnchangedCapture == event = "commit" => committed = version /\ indexed = version
================================================================

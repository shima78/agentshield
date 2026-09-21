from datetime import timezone

from agentshield import AuditLog, Decision, DecisionRequest, Outcome, RiskLevel


def make_request():
    return DecisionRequest(
        actor="agent",
        server="github",
        action="merge_pull_request",
        arguments={},
        context={},
    )


def test_record_appends_event():
    audit = AuditLog()
    audit.record(make_request(), Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok"))
    assert len(audit.events) == 1


def test_record_returns_the_event():
    audit = AuditLog()
    decision = Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok")
    event = audit.record(make_request(), decision)
    assert event.decision == decision


def test_timestamp_is_timezone_aware_utc():
    audit = AuditLog()
    event = audit.record(make_request(), Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok"))
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.utcoffset() == timezone.utc.utcoffset(None)


def test_request_and_decision_are_preserved():
    audit = AuditLog()
    request = make_request()
    decision = Decision.from_outcome(Outcome.DENY, RiskLevel.CRITICAL, "blocked", rule="r1")
    event = audit.record(request, decision)
    assert event.request == request
    assert event.decision == decision


def test_multiple_events_preserve_order():
    audit = AuditLog()
    d1 = Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "first")
    d2 = Decision.from_outcome(Outcome.DENY, RiskLevel.HIGH, "second")
    audit.record(make_request(), d1)
    audit.record(make_request(), d2)
    events = audit.events
    assert [e.decision.reason for e in events] == ["first", "second"]


def test_events_property_returns_a_copy():
    audit = AuditLog()
    audit.record(make_request(), Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok"))
    events = audit.events
    events.append("tampered")
    assert len(audit.events) == 1


def test_approval_fields_default_to_no_approval_involved():
    audit = AuditLog()
    event = audit.record(make_request(), Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok"))
    assert event.approval_required is False
    assert event.approval_outcome is None


def test_approval_fields_can_be_recorded():
    audit = AuditLog()
    decision = Decision.from_outcome(Outcome.REVIEW, RiskLevel.HIGH, "needs review")
    event = audit.record(
        make_request(), decision, approval_required=True, approval_outcome="approved"
    )
    assert event.approval_required is True
    assert event.approval_outcome == "approved"

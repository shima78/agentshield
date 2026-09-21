from datetime import timezone

from agentshield import AuditLog, AuthorizationRequest, Decision, Outcome, RiskLevel


def make_request():
    return AuthorizationRequest(
        actor="agent",
        server="github",
        tool="merge_pull_request",
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

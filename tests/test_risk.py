import pytest

from agentshield import RiskLevel


def test_risk_levels_exist():
    assert RiskLevel.LOW == "low"
    assert RiskLevel.MEDIUM == "medium"
    assert RiskLevel.HIGH == "high"
    assert RiskLevel.CRITICAL == "critical"


def test_risk_level_invalid_value_raises():
    with pytest.raises(ValueError):
        RiskLevel("extreme")

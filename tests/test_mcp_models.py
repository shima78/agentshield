import pytest
from pydantic import ValidationError

from agentshield.mcp import GatewayConfig, GatewayConfigError


def test_gateway_config_from_dict_valid():
    config = GatewayConfig.from_dict(
        {
            "server": {"name": "github"},
            "downstream": {"command": "python", "args": ["server.py"]},
            "policy": {"path": "examples/policy.yaml"},
            "context": {"environment": "production"},
        }
    )
    assert config.server.name == "github"
    assert config.downstream.command == "python"
    assert config.downstream.args == ["server.py"]
    assert config.policy.path == "examples/policy.yaml"
    assert config.context == {"environment": "production"}
    assert config.actor == "agent"


def test_gateway_config_actor_is_overridable():
    config = GatewayConfig.from_dict(
        {
            "server": {"name": "github"},
            "downstream": {"command": "python", "args": ["server.py"]},
            "policy": {"path": "examples/policy.yaml"},
            "actor": "ci-bot",
        }
    )
    assert config.actor == "ci-bot"


def test_gateway_config_missing_server_raises():
    with pytest.raises(ValidationError):
        GatewayConfig.from_dict(
            {
                "downstream": {"command": "python"},
                "policy": {"path": "examples/policy.yaml"},
            }
        )


def test_gateway_config_missing_downstream_raises():
    with pytest.raises(ValidationError):
        GatewayConfig.from_dict(
            {
                "server": {"name": "github"},
                "policy": {"path": "examples/policy.yaml"},
            }
        )


def test_gateway_config_missing_policy_raises():
    with pytest.raises(ValidationError):
        GatewayConfig.from_dict(
            {
                "server": {"name": "github"},
                "downstream": {"command": "python"},
            }
        )


def test_gateway_config_from_yaml_valid(tmp_path):
    yaml_content = """
server:
  name: demo
downstream:
  command: python
  args:
    - examples/mcp_server.py
context:
  environment: production
policy:
  path: examples/mcp_policy.yaml
"""
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml_content, encoding="utf-8")
    config = GatewayConfig.from_yaml(str(path))
    assert config.server.name == "demo"
    assert config.downstream.args == ["examples/mcp_server.py"]


def test_gateway_config_from_yaml_malformed_raises_gateway_config_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("server: [unclosed", encoding="utf-8")
    with pytest.raises(GatewayConfigError):
        GatewayConfig.from_yaml(str(path))


def test_gateway_config_from_yaml_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        GatewayConfig.from_yaml("does-not-exist.yaml")


def test_gateway_config_from_yaml_non_mapping_raises_gateway_config_error(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(GatewayConfigError):
        GatewayConfig.from_yaml(str(path))

"""Architectural guard: the Core must not depend on any optional adapter/provider.

    Core
      ^
      |
    MCP adapter (agentshield.mcp)
    Jev provider (agentshield.jev)
    OpenAI agent provider (agentshield.providers.openai)
    Anthropic agent provider (agentshield.providers.anthropic)

Two complementary checks, run for `mcp`, `jev`/`typesafe_sdk`, `openai`,
and `anthropic`:

1. Static: none of the Core's own source files contain an import
   statement for the optional package (or for the optional adapter
   module that wraps it).
2. Dynamic: a fresh Python process can import and fully use ``agentshield``
   (build a policy, evaluate a decision) even when the optional package is
   made entirely unimportable. This is the authoritative check — it proves
   the Core has no transitive or deferred dependency either, not just no
   top-level import statement.
"""

import pathlib
import subprocess
import sys
import textwrap

CORE_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "agentshield"

# Files that belong to the Core, as opposed to the optional adapters
# (src/agentshield/mcp/, src/agentshield/jev.py,
# src/agentshield/providers/openai.py). agent.py defines the
# provider-agnostic ProposedAction/AgentProvider interface -- it has no
# dependency on any specific provider SDK, so it belongs here too.
CORE_FILES = [
    CORE_DIR / "__init__.py",
    CORE_DIR / "decision.py",
    CORE_DIR / "policy.py",
    CORE_DIR / "engine.py",
    CORE_DIR / "risk.py",
    CORE_DIR / "audit.py",
    CORE_DIR / "semantic.py",
    CORE_DIR / "agent.py",
]


def test_core_source_files_exist():
    # Guards the test itself against silently checking nothing if the
    # Core is ever restructured.
    for path in CORE_FILES:
        assert path.is_file(), f"expected Core file not found: {path}"


def _blocked_import_prefixes_present(module_prefix: str) -> list[str]:
    offending: list[str] = []
    for path in CORE_FILES:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(f"import {module_prefix}") or stripped.startswith(
                f"from {module_prefix}"
            ):
                offending.append(f"{path.name}:{lineno}: {stripped}")
    return offending


def test_core_source_has_no_mcp_import_statements():
    offending = _blocked_import_prefixes_present("mcp")
    assert offending == [], "Core files must not import mcp:\n" + "\n".join(offending)


def test_core_source_has_no_jev_import_statements():
    offending = _blocked_import_prefixes_present(
        "typesafe_sdk"
    ) + _blocked_import_prefixes_present("agentshield.jev")
    assert offending == [], "Core files must not import typesafe_sdk/jev:\n" + "\n".join(
        offending
    )


def test_core_source_has_no_openai_import_statements():
    offending = _blocked_import_prefixes_present("openai") + _blocked_import_prefixes_present(
        "agentshield.providers"
    )
    assert offending == [], "Core files must not import openai/providers:\n" + "\n".join(
        offending
    )


def test_core_source_has_no_anthropic_import_statements():
    offending = _blocked_import_prefixes_present("anthropic")
    assert offending == [], "Core files must not import anthropic:\n" + "\n".join(offending)


def _run_with_blocked_modules(blocked: tuple[str, ...]) -> subprocess.CompletedProcess:
    script = textwrap.dedent(
        f"""
        import sys

        BLOCKED = {blocked!r}

        class _BlockModules:
            def find_module(self, name, path=None):
                return self if any(
                    name == prefix or name.startswith(prefix + ".") for prefix in BLOCKED
                ) else None

            def load_module(self, name):
                raise ImportError(f"intentionally blocked for this test: {{name}}")

        sys.meta_path.insert(0, _BlockModules())

        import agentshield

        engine = agentshield.DecisionEngine(agentshield.Policy(rules=[]))
        request = agentshield.DecisionRequest(actor="agent", action="deploy")
        decision = engine.evaluate(request)
        assert decision.outcome == agentshield.Outcome.ALLOW

        # The provider-agnostic agent interface must also work with none
        # of the optional provider SDKs importable.
        proposal = agentshield.ProposedAction(action="deploy")
        request2 = agentshield.build_decision_request(proposal)
        assert engine.evaluate(request2).outcome == agentshield.Outcome.ALLOW

        print("OK")
        """
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_core_is_fully_usable_with_mcp_import_blocked():
    """Run in a fresh subprocess with `mcp` made unimportable; agentshield
    must still import and evaluate a decision successfully.
    """
    result = _run_with_blocked_modules(("mcp",))
    assert result.returncode == 0, (
        f"agentshield failed to import/run with mcp blocked.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "OK"


def test_core_is_fully_usable_with_jev_import_blocked():
    """Run in a fresh subprocess with `typesafe_sdk` made unimportable;
    agentshield must still import and evaluate a decision successfully.
    This is the strongest form of "Jev disabled/unavailable -> Core still
    works": it proves the Core has zero dependency on the Jev SDK, even
    when no SemanticEvaluator is configured.
    """
    result = _run_with_blocked_modules(("typesafe_sdk",))
    assert result.returncode == 0, (
        f"agentshield failed to import/run with typesafe_sdk blocked.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "OK"


def test_core_is_fully_usable_with_openai_import_blocked():
    """Run in a fresh subprocess with `openai` made unimportable;
    agentshield must still import and evaluate a decision successfully.
    This proves the Core has zero dependency on the OpenAI SDK, even
    when the provider-agnostic ProposedAction/AgentProvider interface is
    used directly.
    """
    result = _run_with_blocked_modules(("openai",))
    assert result.returncode == 0, (
        f"agentshield failed to import/run with openai blocked.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "OK"


def test_core_is_fully_usable_with_anthropic_import_blocked():
    """Run in a fresh subprocess with `anthropic` made unimportable;
    agentshield must still import and evaluate a decision successfully.
    """
    result = _run_with_blocked_modules(("anthropic",))
    assert result.returncode == 0, (
        f"agentshield failed to import/run with anthropic blocked.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "OK"


def test_core_is_fully_usable_with_all_optional_sdks_blocked():
    result = _run_with_blocked_modules(("mcp", "typesafe_sdk", "openai", "anthropic"))
    assert result.returncode == 0, (
        f"agentshield failed to import/run with mcp, typesafe_sdk, openai, "
        f"and anthropic blocked.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "OK"

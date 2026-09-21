"""Architectural guard: the Core must not depend on MCP.

    Core
      ^
      |
    MCP adapter (agentshield.mcp)

Two complementary checks:

1. Static: none of the Core's own source files contain an ``import mcp`` /
   ``from mcp`` statement.
2. Dynamic: a fresh Python process can import and fully use ``agentshield``
   (build a policy, evaluate a decision) even when the ``mcp`` package is
   made entirely unimportable. This is the authoritative check — it proves
   the Core has no transitive or deferred dependency on MCP either, not
   just no top-level import statement.
"""

import pathlib
import subprocess
import sys
import textwrap

CORE_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "agentshield"

# Files that belong to the Core, as opposed to the optional MCP adapter
# (src/agentshield/mcp/).
CORE_FILES = [
    CORE_DIR / "__init__.py",
    CORE_DIR / "decision.py",
    CORE_DIR / "policy.py",
    CORE_DIR / "engine.py",
    CORE_DIR / "risk.py",
    CORE_DIR / "audit.py",
]


def test_core_source_files_exist():
    # Guards the test itself against silently checking nothing if the
    # Core is ever restructured.
    for path in CORE_FILES:
        assert path.is_file(), f"expected Core file not found: {path}"


def test_core_source_has_no_mcp_import_statements():
    offending: list[str] = []
    for path in CORE_FILES:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("import mcp") or stripped.startswith("from mcp"):
                offending.append(f"{path.name}:{lineno}: {stripped}")
    assert offending == [], f"Core files must not import mcp:\n" + "\n".join(offending)


def test_core_is_fully_usable_with_mcp_import_blocked():
    """Run in a fresh subprocess with `mcp` made unimportable; agentshield
    must still import and evaluate a decision successfully.
    """
    script = textwrap.dedent(
        """
        import sys

        class _BlockMCP:
            def find_module(self, name, path=None):
                return self if name == "mcp" or name.startswith("mcp.") else None

            def load_module(self, name):
                raise ImportError(f"mcp is intentionally blocked for this test: {name}")

        sys.meta_path.insert(0, _BlockMCP())

        import agentshield

        engine = agentshield.DecisionEngine(agentshield.Policy(rules=[]))
        request = agentshield.DecisionRequest(actor="agent", action="deploy")
        decision = engine.evaluate(request)
        assert decision.outcome == agentshield.Outcome.ALLOW
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"agentshield failed to import/run with mcp blocked.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "OK"

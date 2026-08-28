"""Guards the layering the package was reorganised into.

The rule is only real if something fails when it is broken. A dependency that points the
wrong way does not break a test or a type check -- it just quietly makes the next move
harder -- so it is asserted here instead of living in a comment nobody re-reads.

`domain` sits at the bottom because `persistence.repository` evaluates anomaly rules while
reading; putting that engine in `services` would have pointed persistence upwards, which
is the mistake this test exists to catch.
"""

from __future__ import annotations

import ast
import re

from tests.support.paths import BACKEND_ROOT, FRONTEND_SOURCE

PACKAGE = BACKEND_ROOT

# A layer may import from itself and from anything listed here, nothing else.
ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "persistence": {"domain"},
    "integrations": {"domain", "persistence"},
    "ingestion": {"domain", "persistence", "integrations"},
    "services": {"domain", "persistence", "integrations"},
    "http": {"domain", "persistence", "integrations", "services"},
    "data_sources": {"domain", "persistence", "integrations", "services", "http"},
}

# Cross-cutting modules at the package root. They carry settings and credential handling,
# which every layer needs and which depend on nothing in return.
ROOT_MODULES = {"config", "security"}

# Entry points, which compose everything and are therefore exempt. `api` and `migrate` are
# additionally pinned by the App Service start command, so they cannot move without a cloud
# change; `accounts` is run by an operator over a shell and is free to move, but it is an
# entry point by the same definition -- it wires config to persistence and is invoked with
# `python -m` rather than imported by anything.
ENTRY_POINTS = {"api", "bootstrap", "migrate", "accounts"}
DEPLOYMENT_ENTRY_POINTS = {"api", "bootstrap", "migrate"}

LAYERS = set(ALLOWED)


def _imported_layers(source: str, own_layer: str) -> set[str]:
    """The layers a module reaches into, resolved from its relative imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom):
            continue
        # level 1 is a sibling inside the same layer; level 2 climbs to the package root.
        if node.level == 1:
            continue
        if node.level >= 2 and node.module:
            head = node.module.split(".")[0]
            if head in LAYERS and head != own_layer:
                found.add(head)
    return found


def test_no_layer_imports_a_layer_above_it() -> None:
    violations: list[str] = []
    for layer, allowed in ALLOWED.items():
        for path in sorted((PACKAGE / layer).rglob("*.py")):
            reached = _imported_layers(path.read_text(encoding="utf-8"), layer)
            for target in sorted(reached - allowed):
                violations.append(f"{layer}/{path.name} imports {target}")
    assert not violations, "Layering violated: " + "; ".join(violations)


def test_every_module_lives_in_a_layer_or_is_an_entry_point() -> None:
    """A new module added at the package root would sit outside the structure entirely."""
    loose = {
        path.stem
        for path in PACKAGE.glob("*.py")
        if path.stem != "__init__"
    }
    assert loose == ROOT_MODULES | ENTRY_POINTS, (
        "Modules at the package root must be an entry point or a declared cross-cutting "
        f"concern; found {sorted(loose)}"
    )


def test_every_backend_package_is_a_declared_layer() -> None:
    packages = {
        path.name
        for path in PACKAGE.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert packages == LAYERS, (
        "Backend packages must be declared architecture layers; "
        f"expected {sorted(LAYERS)}, found {sorted(packages)}"
    )


def test_backend_runtime_never_imports_repository_tests() -> None:
    violations: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^\s*(?:from\s+tests(?:\.|\s)|import\s+tests(?:\.|\s|$))", source):
            violations.append(str(path.relative_to(PACKAGE)))
    assert not violations, f"Runtime modules import repository tests: {violations}"


def test_the_deployment_entry_points_keep_their_import_paths() -> None:
    """`appCommandLine` runs these modules by dotted path.

    Moving either one into a layer would need an App Service configuration change, so the
    coupling is asserted here rather than discovered when a deployment fails to start.
    """
    for name in DEPLOYMENT_ENTRY_POINTS:
        assert (PACKAGE / f"{name}.py").is_file(), (
            f"backend.{name} is referenced by the App Service start "
            "command and must stay at the package root"
        )


def test_the_layers_document_their_own_rule() -> None:
    """Each layer's `__init__` says what it may depend on, so the rule is readable in place."""
    for layer in ALLOWED:
        text = (PACKAGE / layer / "__init__.py").read_text(encoding="utf-8")
        assert re.search(r'^\s*"""', text), f"{layer}/__init__.py needs a docstring"


def test_frontend_never_asserts_an_application_identity_header() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(FRONTEND_SOURCE.rglob("*"))
        if path.suffix in {".ts", ".tsx"}
    )

    assert "X-Hive-Role" not in source
    assert "X-Hive-User" not in source
    assert "system-finops-assistant" not in source


def test_frontend_backend_requests_stay_in_api_layer() -> None:
    violations: list[str] = []
    for path in sorted(FRONTEND_SOURCE.rglob("*")):
        if path.suffix not in {".ts", ".tsx"} or "api" in path.relative_to(FRONTEND_SOURCE).parts:
            continue
        source = path.read_text(encoding="utf-8")
        if 'fetch("/api' in source or "fetch('/api" in source or "apiUrl(" in source:
            violations.append(str(path.relative_to(FRONTEND_SOURCE)))
    assert not violations, f"Backend requests must live under frontend/src/api: {violations}"


def test_frontend_api_layer_does_not_import_react_providers() -> None:
    violations: list[str] = []
    for path in sorted((FRONTEND_SOURCE / "api").rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        if "/providers/" in source or "../providers/" in source:
            violations.append(str(path.relative_to(FRONTEND_SOURCE)))
    assert not violations, f"Frontend API layer imports providers: {violations}"

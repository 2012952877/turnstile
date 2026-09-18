"""Which people a deployment shows an administrator.

The seeded catalogue carries twenty fixture people so the traffic generator can attribute
generated calls to somebody who is deliberately not a real employee. On a demo that roster is
the point. On a customer install it is twenty accounts at a company they have never heard of,
sitting in the budget page, and impossible to delete because nothing stores them -- they are
built in the code on every request.

So it becomes a setting. Default on, because that is what every existing deployment and every
acceptance test already expects; off for a real one.
"""

from __future__ import annotations

import ast
import pathlib

from turnstile_core.config import Settings
from turnstile_core.domain.enterprise import (
    enterprise_catalog,
    governance_directory,
    merge_observed_users,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
GOVERNANCE_MODULES = (
    "backend/services/budget_service.py",
    "backend/http/observability.py",
    "backend/http/model_platform.py",
    "backend/services/assistant.py",
)


def test_the_default_keeps_every_existing_deployment_as_it_was() -> None:
    assert Settings().seed_demo_directory is True
    assert governance_directory(include_seeded_people=True) == enterprise_catalog()


def test_turning_it_off_empties_the_roster_and_nothing_else() -> None:
    directory = governance_directory(include_seeded_people=False)
    seeded = enterprise_catalog()

    assert directory.users == []
    for field in ("organizations", "departments", "projects", "agents"):
        assert getattr(directory, field) == getattr(seeded, field), (
            "only the people go: a discovered person still needs a department to hang under"
        )


def test_the_traffic_generator_keeps_its_fixtures_either_way() -> None:
    """Emptying the seeded list outright would leave generated calls nowhere to land, or
    worse, on a real employee -- spending their budget on synthetic traffic."""
    people = enterprise_catalog().users
    assert len(people) == 20
    assert all(item.id.endswith("@contoso.com") for item in people)


def test_a_real_person_who_used_the_gateway_is_still_listed() -> None:
    """With the fixtures off the roster is empty until someone calls, and then it is them."""
    merged = merge_observed_users(
        governance_directory(include_seeded_people=False),
        [{"user_id": "zhang.san@insilico.ai", "user_ref": "Zhang San",
          "department_id": "department-platform"}],
    )
    assert [item.id for item in merged.users] == ["zhang.san@insilico.ai"]


def test_governance_reads_the_directory_rather_than_the_seeded_catalogue() -> None:
    """A future call site reaching straight for the seeded catalogue puts the fixture people
    back on the screen no matter what the setting says, and the only symptom is twenty
    strangers in somebody's budget page."""
    offenders: list[str] = []
    for relative in GOVERNANCE_MODULES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        offenders.extend(
            f"{relative}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "enterprise_catalog"
        )
    assert not offenders, (
        "governance must go through governance_directory(); these do not: "
        + ", ".join(offenders)
    )

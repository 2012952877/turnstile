from __future__ import annotations

from tests.support.paths import (
    FRONTEND_SOURCE,
    read_apim_dashboard_source,
    read_frontend_styles,
)


def test_product_tables_use_shared_resizable_wrappers() -> None:
    primitive = FRONTEND_SOURCE / "components/ui/resizable-table.tsx"
    raw_native_tables: list[str] = []
    raw_grid_tables: list[str] = []

    for path in FRONTEND_SOURCE.rglob("*.tsx"):
        if path == primitive:
            continue
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(FRONTEND_SOURCE))
        if "<table" in source:
            raw_native_tables.append(relative)
        for line_number, line in enumerate(source.splitlines(), 1):
            if 'role="table"' in line and "<ResizableGridTable" not in line:
                raw_grid_tables.append(f"{relative}:{line_number}")

    assert raw_native_tables == []
    assert raw_grid_tables == []


def test_resizable_table_primitive_keeps_interaction_and_accessibility_contract() -> None:
    source = (
        FRONTEND_SOURCE / "components/ui/resizable-table.tsx"
    ).read_text(encoding="utf-8")
    styles = read_frontend_styles()

    assert "onPointerDown" in source
    assert 'event.key !== "ArrowLeft" && event.key !== "ArrowRight"' in source
    assert 'event.key === "Enter" || event.key === " "' in source
    assert "onDoubleClick" in source
    assert 'role="separator"' in source
    assert "aria-valuemin" in source
    assert "aria-valuemax" in source
    assert "aria-valuenow" in source
    assert "headerSignatureRef" in source
    assert 'window.addEventListener("resize", update)' in source
    assert '["resizable-grid-table", className]' in source
    assert '<span\n        className="table-column-resizer"' in source
    assert '<div\n        className="table-column-resizer"' not in source
    assert "--resizable-table-columns" in source
    assert ".table-column-resizer" in styles
    assert "\t.table-column-resizer," in styles
    assert "display: none" in styles.split("\t.table-column-resizer,", 1)[1].split("}", 1)[0]


def test_visual_disclosure_and_action_tables_are_included() -> None:
    apim_dashboard = read_apim_dashboard_source()
    copilot_governance = (
        FRONTEND_SOURCE / "data-sources/github-copilot/pages/governance-page.tsx"
    ).read_text(encoding="utf-8")

    assert '<ResizableGridTable className="governance-table"' in apim_dashboard
    assert (
        '<ResizableGridTable className="copilot-governance-list copilot-team-list"'
        in copilot_governance
    )
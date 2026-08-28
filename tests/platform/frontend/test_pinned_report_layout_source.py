from tests.support.paths import FRONTEND_SOURCE


def test_pinned_report_layout_is_server_owned_and_committed_once() -> None:
    hook = (
        FRONTEND_SOURCE / "components/reports/use-pinned-card-resize.ts"
    ).read_text(encoding="utf-8")
    page = (FRONTEND_SOURCE / "pages/pinned-report-page.tsx").read_text(encoding="utf-8")
    api = (FRONTEND_SOURCE / "components/assistant/api.ts").read_text(encoding="utf-8")

    assert "localStorage" not in hook
    assert "if (pending) commit(pending)" in hook
    assert "assistantApi.saveLayout" in page
    assert "pinned?.layout" in page
    assert "if (!geometry.canResize) return undefined" in hook
    assert "pinned.can_manage && canResize" in page
    assert '/pinned-charts/${id}/layout' in api
    assert 'method: "PUT"' in api
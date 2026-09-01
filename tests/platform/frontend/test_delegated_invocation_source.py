from tests.support.paths import FRONTEND_SOURCE


def test_assistant_composer_grows_with_its_question_up_to_a_fixed_cap() -> None:
    source = (
        FRONTEND_SOURCE / "components/assistant/assistant-conversation.tsx"
    ).read_text(encoding="utf-8")

    assert "useLayoutEffect(() =>" in source
    assert 'input.style.height = "auto"' in source
    assert "Math.min(input.scrollHeight, 132)" in source
    assert "}, [inputRef, question])" in source


def test_member_invocation_picker_lists_self_and_current_department_testers() -> None:
    invocation = (
        FRONTEND_SOURCE / "data-sources/apim/pages/dashboard-invocation.tsx"
    ).read_text(encoding="utf-8")
    shared = (
        FRONTEND_SOURCE / "data-sources/apim/pages/dashboard-shared.tsx"
    ).read_text(encoding="utf-8")

    assert 'sessionUser.role === "owner"' in invocation
    assert "entities.invocation_testers ?? []" in invocation
    assert "person.parent_id === departmentId" in invocation
    assert "person.id !== sessionUser.email" in invocation
    assert "allowAll={false}" in invocation
    assert "TESTER_ID" not in invocation
    assert "@contoso.com" not in invocation
    assert "allowAll = true" in shared
    assert "allowAll && <SelectItem" in shared
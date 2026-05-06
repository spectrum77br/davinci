from app.schemas.permissions import RESOURCES, Permissions


def test_defaults_fill_all_resources():
    p = Permissions.from_jsonb({}).to_jsonb()
    assert set(p.keys()) == set(RESOURCES)
    for r in RESOURCES:
        assert p[r] == {"view": False, "edit": False, "delete": False}


def test_cascade_delete_implies_edit_and_view():
    p = Permissions.from_jsonb({"produtos": {"delete": True}}).to_jsonb()
    assert p["produtos"] == {"view": True, "edit": True, "delete": True}


def test_cascade_edit_implies_view():
    p = Permissions.from_jsonb({"produtos": {"edit": True}}).to_jsonb()
    assert p["produtos"] == {"view": True, "edit": True, "delete": False}


def test_view_only_does_not_imply_others():
    p = Permissions.from_jsonb({"produtos": {"view": True}}).to_jsonb()
    assert p["produtos"] == {"view": True, "edit": False, "delete": False}


def test_unknown_resource_filtered():
    # Pydantic Literal validation should reject unknown keys.
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Permissions.from_jsonb({"not_a_resource": {"view": True}})

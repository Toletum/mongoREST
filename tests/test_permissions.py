from app.auth import ApiKeyPrincipal, can_list_collections, has_permission


def test_full_access_rule_grants_all_databases_collections_and_actions() -> None:
    principal = ApiKeyPrincipal(
        record_id="test",
        name="full-access",
        permissions=[{"database": "*", "actions": ["*"]}],
    )

    assert has_permission(principal, "sales", "read", "orders")
    assert has_permission(principal, "inventory", "admin")
    assert has_permission(principal, "sales", "diagnostics")


def test_collection_scoped_rule_does_not_grant_other_collections() -> None:
    principal = ApiKeyPrincipal(
        record_id="test",
        name="reader",
        permissions=[
            {"database": "sales", "collections": ["orders"], "actions": ["read"]}
        ],
    )

    assert has_permission(principal, "sales", "read", "orders")
    assert not has_permission(principal, "sales", "write", "orders")
    assert not has_permission(principal, "sales", "read", "customers")
    assert not has_permission(principal, "inventory", "read", "orders")
    assert not has_permission(principal, "sales", "read")
    assert not has_permission(principal, "sales", "admin")
    assert can_list_collections(principal, "sales")
    assert not can_list_collections(principal, "inventory")


def test_collection_admin_does_not_grant_database_level_admin() -> None:
    principal = ApiKeyPrincipal(
        record_id="test",
        name="collection-admin",
        permissions=[
            {"database": "sales", "collections": ["orders"], "actions": ["admin"]}
        ],
    )

    assert has_permission(principal, "sales", "admin", "orders")
    assert not has_permission(principal, "sales", "admin")


def test_database_rule_without_collection_scope_grants_all_collections() -> None:
    principal = ApiKeyPrincipal(
        record_id="test",
        name="database-reader",
        permissions=[{"database": "sales", "actions": ["read"]}],
    )

    assert has_permission(principal, "sales", "read", "orders")
    assert has_permission(principal, "sales", "read", "customers")
    assert not has_permission(principal, "sales", "write", "orders")

from app.auth.service import allows, digest, new_tokens


def test_role_factory_scope_fails_closed() -> None:
    assert allows("manager", "quality", None)
    assert allows("sales", "sales")
    assert not allows("sales", "quality")
    assert not allows("sales", "sales", 1)
    assert allows("production", "production", 1)
    assert allows("production", "quality", 1)
    assert not allows("production", "production", 2)
    assert not allows("production", "production", None)
    assert not allows("production", "sales", 1)
    assert not allows("it_admin", "sales")
    assert not allows("unknown", "sales")


def test_session_tokens_are_independent_and_only_digests_are_stored() -> None:
    access, refresh = new_tokens()
    assert access != refresh
    assert access != digest(access)
    assert digest(access) != digest(refresh)


def test_demo_login_is_off_unless_switched_on() -> None:
    from app.core.config import Settings

    common = {"_env_file": None, "warehouse_password": "x", "app_db_password": "x"}
    assert Settings(**common).demo_login_enabled is False
    assert Settings(demo_login_enabled=True, **common).demo_login_enabled is True

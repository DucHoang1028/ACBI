from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.auth.service import (
    allows,
    demo_login,
    demo_users,
    login,
    revoke,
    rotate,
    user_for_token,
)

router = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)
COOKIE = "acbi_refresh"


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


def invalid() -> HTTPException:
    return HTTPException(status_code=401, detail="Invalid credentials")


def set_refresh(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/auth",
        max_age=7 * 86400,
    )


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Invalid origin")


def current_user(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise invalid()
    user = user_for_token(request.app.state.storage, credentials.credentials)
    if user is None:
        raise invalid()
    return user


@router.post("/auth/login")
def sign_in(body: Credentials, request: Request, response: Response) -> dict[str, str]:
    same_origin(request)
    tokens = login(request.app.state.storage, body.username, body.password)
    if tokens is None:
        raise invalid()
    access, refresh = tokens
    set_refresh(response, refresh, request)
    return {"access_token": access, "token_type": "bearer"}


class DemoAccount(BaseModel):
    username: str = Field(min_length=1, max_length=80)


def demo_enabled(request: Request) -> bool:
    return bool(request.app.state.settings.demo_login_enabled)


@router.get("/auth/demo-users")
def demo_accounts(request: Request) -> list[dict[str, str]]:
    """Accounts for one-click sign-in; empty unless the demo switch is on."""
    return demo_users(request.app.state.storage) if demo_enabled(request) else []


@router.post("/auth/demo-login")
def demo_sign_in(
    body: DemoAccount, request: Request, response: Response
) -> dict[str, str]:
    same_origin(request)
    if not demo_enabled(request):
        raise HTTPException(status_code=404, detail="Not found")
    tokens = demo_login(request.app.state.storage, body.username)
    if tokens is None:
        raise invalid()
    access, refresh = tokens
    set_refresh(response, refresh, request)
    return {"access_token": access, "token_type": "bearer"}


@router.post("/auth/refresh")
def renew(request: Request, response: Response) -> dict[str, str]:
    same_origin(request)
    token = request.cookies.get(COOKIE)
    tokens = rotate(request.app.state.storage, token) if token else None
    if tokens is None:
        raise invalid()
    access, refresh = tokens
    set_refresh(response, refresh, request)
    return {"access_token": access, "token_type": "bearer"}


@router.post("/auth/logout")
def sign_out(request: Request, response: Response) -> dict[str, str]:
    same_origin(request)
    token = request.cookies.get(COOKIE)
    if token:
        revoke(request.app.state.storage, token)
    response.delete_cookie(COOKIE, path="/api/auth")
    return {"status": "signed_out"}


@router.get("/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    role = user["role"]
    return {
        "username": user["username"],
        "role": role,
        "domains": [
            d
            for d in ("sales", "production", "quality")
            if allows(role, d, 1 if role == "production" else None)
        ],
        "factories": (
            [1] if role == "production" else "all" if role == "manager" else []
        ),
    }


@router.get("/metadata")
def metadata(
    request: Request, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    dictionary = request.app.state.dictionary
    role = user["role"]
    metrics = [
        {
            "metric_id": m["metricId"],
            "name": m["name"],
            "domain": m["domain"],
            "version": next(
                d["version"]
                for d in m["definitions"]
                if d["approvalStatus"] == "approved"
            ),
            "dimensions": m["supportedDimensions"],
        }
        for m in dictionary["businessMetrics"]
        if any(d["approvalStatus"] == "approved" for d in m["definitions"])
        and allows(role, m["domain"], 1 if role == "production" else None)
    ]
    return {
        "metrics": metrics,
        "factory_scope": (
            [1] if role == "production" else "all" if role == "manager" else []
        ),
    }


@router.get("/admin/status")
def admin_status(
    request: Request, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    if user["role"] != "it_admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return {
        "status": "ok",
        "phase": request.app.state.readiness["phase"],
        "data_as_of": request.app.state.readiness["data_as_of"],
    }

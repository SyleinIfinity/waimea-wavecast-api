import secrets

from fastapi import Header, HTTPException, Request


def _verify(value: str | None, expected: str) -> None:
    if not expected or not value or not secrets.compare_digest(value, expected):
        raise HTTPException(status_code=401, detail="invalid credentials")


def internal_auth(request: Request, x_internal_token: str | None = Header(default=None)) -> None:
    _verify(x_internal_token, request.app.state.settings.internal_api_token)


def admin_auth(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    _verify(x_admin_token, request.app.state.settings.admin_api_token)

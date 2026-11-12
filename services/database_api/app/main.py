import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import Error as PostgresError
from psycopg_pool import PoolTimeout

from services.database_api.app.api.admin import router as admin_router
from services.database_api.app.api.internal import router as internal_router
from services.database_api.app.api.public import router as public_router
from services.database_api.app.config import Settings, get_settings
from services.database_api.app.db.session import Database


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = database or Database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        db.close()

    app = FastAPI(title="Waimea WaveCast Database API", version="1.0.0", lifespan=lifespan, docs_url="/docs" if settings.app_env != "production" else None)
    app.state.settings = settings
    app.state.db = db
    app.state.previews = {}
    app.add_middleware(CORSMiddleware, allow_origins=[settings.public_origin], allow_methods=["GET", "POST", "PATCH", "PUT"], allow_headers=["Content-Type", "X-Admin-Token", "X-Actor-ID", "X-Request-ID", "Idempotency-Key"], allow_credentials=False)
    app.include_router(public_router)
    app.include_router(internal_router)
    app.include_router(admin_router)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        identifier = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = identifier
        response = await call_next(request)
        response.headers["X-Request-ID"] = identifier
        return response

    @app.exception_handler(PostgresError)
    async def database_error(request: Request, exc: PostgresError):
        logging.getLogger(__name__).error("database failure sqlstate=%s", exc.sqlstate)
        if exc.sqlstate in {"23503", "23514", "22P02", "22007", "22008"}:
            status, code, message = 422, "INVALID_DATA", "data violates database constraints"
        elif exc.sqlstate == "23505":
            status, code, message = 409, "CONFLICT", "record already exists"
        else:
            status, code, message = 503, "DATABASE_UNAVAILABLE", "database request failed"
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "request_id": request.state.request_id}})

    @app.exception_handler(PoolTimeout)
    async def pool_error(request: Request, exc: PoolTimeout):
        return JSONResponse(status_code=503, content={"error": {"code": "DATABASE_UNAVAILABLE", "message": "database pool timed out", "request_id": request.state.request_id}})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 422: "INVALID_PARAMETER", 503: "DEPENDENCY_UNAVAILABLE"}
        detail = exc.detail
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": codes.get(exc.status_code, "HTTP_ERROR"), "message": detail if isinstance(detail, str) else "request failed", "details": detail if isinstance(detail, dict) else {}, "request_id": request.state.request_id}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(part) for part in issue["loc"]), "message": issue["msg"]} for issue in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "INVALID_PARAMETER", "message": "request validation failed", "details": details, "request_id": request.state.request_id}})

    @app.get("/health/live")
    def live():
        return {"status": "live", "service": "database-api"}

    @app.get("/health")
    @app.get("/health/ready")
    def ready():
        try:
            if db.ready():
                return {"status": "ready", "service": "database-api"}
        except Exception:
            pass
        raise HTTPException(503, "database unavailable")

    return app


app = create_app()

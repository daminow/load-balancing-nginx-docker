from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import make_engine, make_sessionmaker


async def _wait_for_database(engine: AsyncEngine, logger: structlog.stdlib.BoundLogger) -> None:
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(30),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    ):
        with attempt:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("database.ready")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("app.main")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings)
        await _wait_for_database(engine, logger)
        app.state.engine = engine
        app.state.session_factory = make_sessionmaker(engine)
        logger.info("startup.complete", instance=settings.instance_id)
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("shutdown.complete")

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )

    @app.middleware("http")
    async def add_instance_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Served-By"] = settings.instance_id
        return response

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        get_logger("app.errors").exception("unhandled.exception", path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"},
        )

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "instance": settings.instance_id,
            "docs": "/docs",
        }

    return app


app = create_app()

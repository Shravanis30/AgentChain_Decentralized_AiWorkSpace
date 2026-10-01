from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.agents import router as agents_router
from app.api.v1.auth import router as auth_router
from app.api.v1.distributions import router as distributions_router
from app.api.v1.executions import router as executions_router
from app.api.v1.health import router as health_router
from app.api.v1.marketplace import router as marketplace_router
from app.api.v1.notarizations import router as notarizations_router
from app.api.v1.reputation import router as reputation_router
from app.api.v1.reputation_scoring import router as reputation_scoring_router
from app.api.v1.operator import router as operator_router
from app.api.v1.orchestrations import router as orchestrations_router
from app.api.v1.settlements import router as settlements_router
from app.api.v1.users import router as users_router
from app.core.config import settings
from app.core.metrics import metrics


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Lifespan startup hooks can go here
    yield
    # Lifespan shutdown hooks


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(executions_router, prefix="/api/v1")
    app.include_router(orchestrations_router, prefix="/api/v1")
    app.include_router(operator_router, prefix="/api/v1")
    app.include_router(settlements_router, prefix="/api/v1")
    app.include_router(distributions_router, prefix="/api/v1")
    app.include_router(notarizations_router, prefix="/api/v1")
    app.include_router(reputation_router, prefix="/api/v1")
    app.include_router(reputation_scoring_router, prefix="/api/v1")
    app.include_router(marketplace_router, prefix="/api/v1")

    @app.get("/metrics", summary="Prometheus metrics exposition endpoint")
    @app.get("/api/v1/metrics", summary="Prometheus metrics exposition endpoint")
    async def get_metrics() -> Response:
        content = metrics.generate_prometheus_text()
        return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


app = create_app()

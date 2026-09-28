from pydantic import BaseModel, Field


class DependencyHealth(BaseModel):
    database: str
    redis: str
    qdrant: str
    object_storage: str


class HealthResponse(BaseModel):
    status: str = Field(description="'ok' if all dependencies healthy, 'degraded' otherwise")
    version: str
    dependencies: DependencyHealth

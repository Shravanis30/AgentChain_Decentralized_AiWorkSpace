from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "AgentChain"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    SECRET_KEY: str = "dev_insecure_secret_key_change_in_production_min32chars"

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://agentchain_user:agentchain_dev_password@localhost:5432/agentchain_db"
    )
    DATABASE_SYNC_URL: str = (
        "postgresql://agentchain_user:agentchain_dev_password@localhost:5432/agentchain_db"
    )

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str | None = None

    # Object Storage (MinIO / S3)
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_ACCESS_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "agentchain-artifacts"
    S3_REGION: str = "us-east-1"

    # Blockchain
    RPC_URL: str = "http://localhost:8545"
    CHAIN_ID: int = 31337
    ALLOWED_CHAIN_IDS: list[int] = [31337, 84532, 8453]

    # SIWE & Auth
    SIWE_DOMAIN: str = "localhost:3000"
    SIWE_ALLOWED_DOMAINS: list[str] = [
        "localhost:3000",
        "127.0.0.1:3000",
        "localhost",
        "127.0.0.1",
    ]
    NONCE_EXPIRE_MINUTES: int = 5
    SESSION_EXPIRE_DAYS: int = 7
    SESSION_COOKIE_NAME: str = "agentchain_session"
    SESSION_COOKIE_SECURE: bool = False  # Set to True in production (HTTPS)
    SESSION_COOKIE_SAMESITE: str = "lax"
    SESSION_COOKIE_DOMAIN: str | None = None

    # Rate Limiting
    RATE_LIMIT_NONCE_LIMIT: int = 30
    RATE_LIMIT_VERIFY_LIMIT: int = 15
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Phase 5: Multi-Agent Orchestration Limits
    MAX_GRAPH_TASKS: int = 20
    MAX_GRAPH_DEPTH: int = 10
    MAX_DEPENDENCIES_PER_TASK: int = 5
    MAX_CONCURRENT_TASKS_PER_ORCHESTRATION: int = 3
    MAX_ORCHESTRATIONS_PER_USER: int = 50
    MAX_TASK_INPUT_BYTES: int = 65536  # 64 KB
    MAX_TASK_OUTPUT_BYTES: int = 131072  # 128 KB
    MAX_ARTIFACT_BYTES: int = 1048576  # 1 MB
    MAX_CHECKPOINT_BYTES: int = 262144  # 256 KB
    MAX_ORCHESTRATION_RUNTIME_SECONDS: int = 3600  # 1 hour

    # Phase 5.2: Durable Orchestration Wake-Up & Recovery
    ORCHESTRATION_WAKEUP_STREAM: str = "agentchain:orchestration:wakeup_stream"
    ORCHESTRATION_WAKEUP_GROUP: str = "agentchain:orchestration:wakeups_group"
    ORCHESTRATION_RECOVERY_INTERVAL_SECONDS: float = 5.0
    ORCHESTRATION_RECOVERY_STALE_THRESHOLD_SECONDS: float = 10.0
    ORCHESTRATION_WAKEUP_DISPATCH_BATCH_SIZE: int = 50


settings = Settings()


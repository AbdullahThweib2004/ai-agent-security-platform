"""Application configuration, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "AI Agent Security & Control Platform"
    api_prefix: str = ""

    postgres_dsn: str = "postgresql+psycopg://aasec:aasec_pw@localhost:5432/aasec"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "aasec_neo4j_pw"

    cors_origins: str = "http://localhost:5173"

    log_level: str = "INFO"
    # "json" everywhere by default so what runs in production is what is tested;
    # LOG_FORMAT=text is available for local reading.
    log_format: str = "json"
    health_timeout_seconds: float = 2.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""All configuration in one typed place, loaded from environment variables or .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secrets are SecretStr so they never show up in logs or tracebacks.
    model_api_key: SecretStr | None = None
    mapillary_token: SecretStr | None = None
    db_client_id: SecretStr | None = None
    db_api_key: SecretStr | None = None

    meta_base_url: str = "https://api.meta.ai/v1"
    spark_model: str = "muse-spark-1.3"
    sam_model: str = "sam-3.1"

    # Demo area as (min_lon, min_lat, max_lon, max_lat): Darmstadt Hbf to Luisenplatz.
    bbox: tuple[float, float, float, float] = (8.628, 49.869, 8.653, 49.876)
    data_dir: Path = Path("data")
    # Photos farther than this from any footpath are ignored.
    max_photo_distance_m: float = Field(default=15.0, gt=0)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "barrierfrei.sqlite"


# Named walking areas as (min_lon, min_lat, max_lon, max_lat). A trip walks inside one area at
# each end and rides transit in between, so we only download the streets we actually walk.
AREAS: dict[str, tuple[float, float, float, float]] = {
    "darmstadt": (8.628, 49.869, 8.653, 49.876),  # Darmstadt Hbf to Luisenplatz
    "frankfurt": (8.660, 50.104, 8.692, 50.118),  # Frankfurt Hbf, Hauptwache, Konstablerwache, Zeil
}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require(secret: SecretStr | None, name: str) -> str:
    if secret is None or not secret.get_secret_value():
        raise RuntimeError(f"{name} is not set; add it to .env")
    return secret.get_secret_value()

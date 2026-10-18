from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAS_", env_file=ROOT / ".env", extra="ignore"
    )
    host: str = "127.0.0.1"
    port: int = 8000
    # Extra Host names allowed besides localhost, comma-separated (e.g. a reverse-proxy domain).
    allowed_hosts: str = ""
    storage_dir: Path = ROOT / "storage"
    max_upload_mb: int = 150
    max_duration_seconds: int = 300
    mongo_uri: str = ""
    mongo_database: str = "music_analysis_studio"
    ssh_alias: str = ""
    ssh_remote_host: str = "127.0.0.1"
    ssh_remote_port: int = 27017
    ssh_local_port: int = 27018
    ai_url: str = ""
    ai_model: str = ""
    ai_api_key: str = ""

    def prepare(self):
        if not self.storage_dir.is_absolute():
            self.storage_dir = ROOT / self.storage_dir
        for name in ("uploads", "audio", "cache", "projects"):
            (self.storage_dir / name).mkdir(parents=True, exist_ok=True)

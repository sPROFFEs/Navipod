from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Security
    SECRET_KEY: str = "unsafe-default-secret-key-change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    REMEMBER_SESSION_DAYS: int = 30

    # Paths
    MUSIC_ROOT: str = "/saas-data/users"
    COOKIES_FILE: str = "cookies.txt"

    # External Services
    SPOTIFY_CLIENT_ID: str | None = None
    SPOTIFY_CLIENT_SECRET: str | None = None

    # Navidrome internal auth (used by concierge↔Navidrome Subsonic calls)
    # Override via NAVIDROME_INTERNAL_PASSWORD env var in production.
    NAVIDROME_INTERNAL_PASSWORD: str = "enc:000000"

    # Infrastructure
    CHECK_INTERVAL_MINUTES: int = 30
    NAVIDROME_IMAGE: str = "deluan/navidrome:latest"
    HOST_DATA_ROOT: str = "/opt/saas-data"
    BACKUP_ROOT: str = "/saas-data/backups"
    APP_SOURCE_ROOT: str = "/workspace"
    COMPOSE_ENV_FILE: str = "/saas-data/config/navipod.env"
    RUNTIME_ENV_FILE: str = "/run/navipod/.env"
    CONCURRENT_DOWNLOADS: int = 3
    DOWNLOADER_WORKER_URL: str = "http://downloader:8081"
    DOWNLOADER_WORKER_TOKEN_FILE: str = "/saas-data/download-staging/.worker-token"
    DOWNLOADER_STAGING_ROOT: str = "/saas-data/download-staging"
    DOWNLOADER_WORKER_HEALTH_TIMEOUT_SECONDS: float = 2.0
    DOWNLOADER_WORKER_JOB_TIMEOUT_SECONDS: int = 2700
    DOWNLOADER_WORKER_POLL_SECONDS: float = 1.0
    POOL_STATUS_CACHE_TTL_SECONDS: int = 60
    COOKIE_SECURE: bool = True
    TRUST_PROXY_HEADERS: bool = False
    TRUSTED_PROXY_IPS: str = "127.0.0.1,::1"
    BACKUP_SCHEDULER_POLL_SECONDS: int = 60
    UPDATE_SOURCE_REPO_URL: str = "https://github.com/sPROFFEs/Navipod"
    UPDATE_SOURCE_BRANCH: str = "main"
    UPDATE_MANAGED_SERVICES: str = "concierge"
    NAVIDROME_REVERSE_PROXY_WHITELIST: str = "127.0.0.1/32,172.16.0.0/12"
    PROXY_IMAGE_MAX_BYTES: int = 5 * 1024 * 1024
    PROXY_IMAGE_TIMEOUT_SECONDS: float = 8.0
    PROXY_IMAGE_ALLOWED_CONTENT_TYPES: str = "image/jpeg,image/png,image/webp,image/gif,image/avif"
    STARTUP_LOUDNESS_BACKFILL: bool = False

    # Allowed Hosts (CORS & TrustedHost)
    DOMAIN: str = "localhost"
    ALLOWED_HOSTS: str = "localhost,127.0.0.1,0.0.0.0,domain.com,*.domain.com"
    CORS_ORIGINS: str = "http://localhost,http://127.0.0.1"

    @property
    def all_allowed_hosts(self) -> list[str]:
        hosts = [
            host.strip()
            for host in self.ALLOWED_HOSTS.split(",")
            if host.strip() and host.strip() not in {"domain.com", "*.domain.com"}
        ]
        if self.DOMAIN and self.DOMAIN != "localhost":
            hosts.append(self.DOMAIN)
            hosts.append(f"*.{self.DOMAIN}")
        return sorted(set(hosts))

    @property
    def trusted_proxy_ips(self) -> set[str]:
        return {ip.strip() for ip in (self.TRUSTED_PROXY_IPS or "").split(",") if ip.strip()}

    @property
    def cors_origins(self) -> list[str]:
        origins = {origin.strip().rstrip("/") for origin in self.CORS_ORIGINS.split(",") if origin.strip()}
        if self.DOMAIN and self.DOMAIN != "localhost":
            origins.add(f"https://{self.DOMAIN}")
        return sorted(origins)

    @property
    def proxy_image_allowed_content_types(self) -> set[str]:
        return {
            content_type.strip().lower()
            for content_type in (self.PROXY_IMAGE_ALLOWED_CONTENT_TYPES or "").split(",")
            if content_type.strip()
        }


settings = Settings()

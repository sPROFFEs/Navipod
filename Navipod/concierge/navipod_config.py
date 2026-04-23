from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Security
    SECRET_KEY: str = "unsafe-default-secret-key-change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440 # 24 hours

    # Paths
    MUSIC_ROOT: str = "/saas-data/users"
    COOKIES_FILE: str = "cookies.txt"
    
    # External Services
    SPOTIFY_CLIENT_ID: str | None = None
    SPOTIFY_CLIENT_SECRET: str | None = None
    
    # Infrastructure
    CHECK_INTERVAL_MINUTES: int = 30
    NAVIDROME_IMAGE: str = "deluan/navidrome:latest"
    HOST_DATA_ROOT: str = "/opt/saas-data"
    BACKUP_ROOT: str = "/saas-data/backups"
    APP_SOURCE_ROOT: str = "/workspace"
    COMPOSE_ENV_FILE: str = "/saas-data/config/navipod.env"
    RUNTIME_ENV_FILE: str = "/run/navipod/.env"
    CONCURRENT_DOWNLOADS: int = 3
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
    
    # Allowed Hosts (CORS & TrustedHost)
    DOMAIN: str = "localhost"
    ALLOWED_HOSTS: str = "localhost,127.0.0.1,0.0.0.0,domain.com,*.domain.com"
    
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
        return {
            ip.strip()
            for ip in (self.TRUSTED_PROXY_IPS or "").split(",")
            if ip.strip()
        }

    @property
    def proxy_image_allowed_content_types(self) -> set[str]:
        return {
            content_type.strip().lower()
            for content_type in (self.PROXY_IMAGE_ALLOWED_CONTENT_TYPES or "").split(",")
            if content_type.strip()
        }
    
    class Config:
        env_file = ".env"
        # Environment variables will take precedence over .env file
        # Case insensitive matching (e.g. secret_key matches SECRET_KEY)

settings = Settings()

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
    COOKIE_SECURE: bool = True
    BACKUP_SCHEDULER_POLL_SECONDS: int = 60
    UPDATE_SOURCE_REPO_URL: str = "https://github.com/sPROFFEs/Navipod"
    UPDATE_SOURCE_BRANCH: str = "main"
    UPDATE_MANAGED_SERVICES: str = "concierge nginx tunnel"
    
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
    
    class Config:
        env_file = ".env"
        # Environment variables will take precedence over .env file
        # Case insensitive matching (e.g. secret_key matches SECRET_KEY)

settings = Settings()

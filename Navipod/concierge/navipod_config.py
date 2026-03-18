from pydantic import field_validator
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
    CONCURRENT_DOWNLOADS: int = 3
    COOKIE_SECURE: bool = True
    
    # Allowed Hosts (CORS & TrustedHost)
    DOMAIN: str = "localhost"
    ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1", "0.0.0.0", "domain.com", "*.domain.com"]

    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, value):
        if value is None:
            return ["localhost", "127.0.0.1", "0.0.0.0", "domain.com", "*.domain.com"]
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value
    
    @property
    def all_allowed_hosts(self) -> list[str]:
        hosts = [host for host in self.ALLOWED_HOSTS if host not in {"domain.com", "*.domain.com"}]
        if self.DOMAIN and self.DOMAIN != "localhost":
            hosts.append(self.DOMAIN)
            hosts.append(f"*.{self.DOMAIN}")
        return sorted(set(hosts))
    
    class Config:
        env_file = ".env"
        # Environment variables will take precedence over .env file
        # Case insensitive matching (e.g. secret_key matches SECRET_KEY)

settings = Settings()

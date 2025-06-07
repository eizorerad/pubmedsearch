from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class AppSettings(BaseSettings):
    """
    Manages application configuration by reading variables from .env file and environment.
    """
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # NCBI API Settings
    NCBI_API_KEY: Optional[str] = None
    EUTILS_BASE_URL: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    # Concurrency Limits
    CONCURRENCY_LIMIT_WITH_KEY: int = 10
    CONCURRENCY_LIMIT_WITHOUT_KEY: int = 3
    
    # Cache Settings
    CACHE_TTL: int = 3600 # in seconds (1 hour)

    # Redis Settings
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    # Application Settings
    APP_TITLE: str = "PubMed Search API"
    APP_VERSION: str = "2.0.0"
    APP_DESCRIPTION: str = "An enhanced API for searching PubMed articles, with modular logic and configuration management."
    
    # API Security Key
    API_KEY: str = "YOUR_SECRET_API_KEY"

# Create a single instance of settings to be used throughout the application
settings = AppSettings()

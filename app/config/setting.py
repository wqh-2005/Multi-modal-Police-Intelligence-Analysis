from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()

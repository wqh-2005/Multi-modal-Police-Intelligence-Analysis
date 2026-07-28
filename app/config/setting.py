from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    api_key:str
    base_url:str
    model_name:str
    model_config = SettingsConfigDict(env_file=".env")
settings = Settings()
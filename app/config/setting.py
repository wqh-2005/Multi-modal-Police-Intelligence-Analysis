from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
    # 音频转写模型（硅基流动不支持 OpenAI 的 whisper-1，需显式指定平台模型）
    audio_model: str = "FunAudioLLM/SenseVoiceSmall"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()

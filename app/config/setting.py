from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # 统一使用硅基流动密钥（SILICONFLOW_API_KEY 优先，兼容旧 API_KEY 配置）
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("SILICONFLOW_API_KEY", "API_KEY"),
    )
    base_url: str = ""
    model_name: str = ""
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
    # 音频转写模型（硅基流动不支持 OpenAI 的 whisper-1，需显式指定平台模型）
    audio_model: str = "FunAudioLLM/SenseVoiceSmall"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    timeout : int
    video_model: str = ""
settings = Settings()

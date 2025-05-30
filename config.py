# config.py
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    bot_token: SecretStr

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8"
    )


settings = BotSettings()

# Для прямого доступа, если не хотите везде использовать settings.bot_token
BOT_TOKEN = settings.bot_token.get_secret_value()

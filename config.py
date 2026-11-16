from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

KYIV = ZoneInfo('Europe/Kyiv')


@dataclass
class Config:
    api_id: int
    api_hash: str
    session_name: str | None   # local file session (dima.session)
    session_value: str | None  # StringSession, used on Azure
    chat_name: str
    bot_channel: str
    open_ai_key: str
    model: str
    triage_model: str
    alerts_live: bool
    state_dir: str
    port: int
    daily_hour: int
    alert_interval_min: int


def load_config(path: str = 'config.ini') -> Config:
    """Environment variables win over config.ini (Azure uses app settings, locally config.ini)."""
    ini = configparser.ConfigParser()
    ini.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), path))

    def get(env: str, section: str | None = None, key: str | None = None, default=None):
        value = os.environ.get(env)
        if value:
            return value
        if section:
            return ini.get(section, key or env.lower(), fallback=default)
        return default

    session_value = os.environ.get('SESSION_VALUE')
    return Config(
        api_id=int(get('API_ID', 'telegram', 'api_id')),
        api_hash=get('API_HASH', 'telegram', 'api_hash'),
        # an explicit StringSession always wins over the local session file
        session_name=None if session_value else ini.get('telegram', 'session_name', fallback=None),
        session_value=session_value,
        chat_name=get('CHAT_NAME', 'telegram', 'chat_name'),
        bot_channel=get('BOT_CHANNEL', 'telegram', 'bot_channel'),
        open_ai_key=get('OPENAI_API_KEY', 'openai', 'api_key'),
        model=get('MODEL', default='gpt-5.4'),
        triage_model=get('TRIAGE_MODEL', default='gpt-5.4-mini'),
        alerts_live=get('ALERTS_LIVE', default='false').lower() == 'true',
        state_dir=get('STATE_DIR', default='.'),
        port=int(get('PORT', default='8000')),
        daily_hour=int(get('DAILY_HOUR', default='8')),
        alert_interval_min=int(get('ALERT_INTERVAL_MIN', default='10')),
    )


def make_client(cfg: Config):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if cfg.session_value:
        session = StringSession(cfg.session_value)
    else:
        session = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg.session_name)
    return TelegramClient(session, cfg.api_id, cfg.api_hash)

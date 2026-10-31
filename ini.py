from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import asyncio
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession
import configparser
import os

from messages_functions import format_messages, get_chat_messages, get_output_message, format_history
from summarizer import summarize

# Read configuration from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

api_id = os.environ.get('API_ID', config.get('telegram', 'api_id', fallback=None))
api_hash = os.environ.get('API_HASH', config.get('telegram', 'api_hash', fallback=None))
session_name = config.get('telegram', 'session_name', fallback=None)
session_value = os.environ.get('SESSION_VALUE')
chat_input = os.environ.get('CHAT_NAME', config.get('telegram', 'chat_name', fallback=None))
bot_channel = os.environ.get('BOT_CHANNEL', config.get('telegram', 'bot_channel', fallback=None))
open_ai_key = os.environ.get('OPENAI_API_KEY', config.get('openai', 'api_key', fallback=None))

# Print the summary instead of posting it: python ini.py --dry-run
dry_run = '--dry-run' in sys.argv

KYIV = ZoneInfo('Europe/Kyiv')
HISTORY_DAYS = 7

async def main(chat_name):
    client = TelegramClient(session_name if session_name else StringSession(session_value), api_id, api_hash)
    await client.start()

    chat_info = await client.get_entity(chat_name)
    bot_channel_info = await client.get_entity(bot_channel)

    target_date = datetime.now(KYIV).date() - timedelta(days=1)
    #target_date = datetime(2024, 12, 5).date()  # Specific date
    start_time = datetime.combine(target_date, time.min, tzinfo=KYIV)
    end_time = start_time + timedelta(days=1)

    # summaries posted during the last week (each one covers the day before it was posted)
    summary_messages = await get_chat_messages(client, bot_channel_info, end_time - timedelta(days=HISTORY_DAYS), end_time)
    formatted_history = await format_history(summary_messages, KYIV)

    messages = await get_chat_messages(client, chat_info, start_time, end_time)
    if not messages:
        print("No messages found.")
        return
    messages.reverse()
    formatted_messages = await format_messages(messages)
    if not formatted_messages:
        print("No messages found 2.")
        return

    summary = summarize(open_ai_key, formatted_messages, target_date, formatted_history)
    if not summary:
        print("Empty summary.")
        return

    output_message = get_output_message(summary, len(messages), formatted_messages)

    if dry_run:
        print(output_message)
        return

    # Send a message to the channel
    await client.send_message(bot_channel_info, output_message)
    print(f"Posted summary for {target_date}: {len(messages)} messages, {len(summary_messages)} previous summaries")


asyncio.run(main(chat_name=chat_input))

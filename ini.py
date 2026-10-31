from datetime import datetime, time, timedelta
import asyncio
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
bot_token = os.environ.get('BOT_TOKEN', config.get('telegram', 'bot_token', fallback=None))
bot_channel = os.environ.get('BOT_CHANNEL', config.get('telegram', 'bot_channel', fallback=None))
open_ai_key = os.environ.get('OPENAI_API_KEY', config.get('openai', 'api_key', fallback=None))

async def main(chat_name):
    client = TelegramClient(session_name if session_name else StringSession(session_value), api_id, api_hash)
    await client.start()

    chat_info = await client.get_entity(chat_name)
    bot_channel_info = await client.get_entity(bot_channel)
    
    session_string = client.session.save()
    print("Your session string is: ", session_string)

    target_date = datetime.now().date() - timedelta(days=1)
    #target_date = datetime(2024, 12, 5).date()  # Specific date

    summary_messages_day_before = await get_chat_messages(client, bot_channel_info, target_date)
    formatted_history = await format_history(summary_messages_day_before)

    messages = await get_chat_messages(client, chat_info, target_date)
    if not messages:
        print("No messages found.")
        return
    messages.reverse()
    formatted_messages = await format_messages(messages)
    if not formatted_messages:
        print("No messages found 2.")
        return
    formatted_history = await format_history(summary_messages_day_before)
    
    (key_topics, title) = summarize(open_ai_key, formatted_messages, target_date, formatted_history)
    if not key_topics:
        print("No key topics found.")
        return

    output_message = get_output_message(key_topics, title, len(messages), formatted_messages)
    
    #Send a message to the channel
    #await client.start(bot_token=bot_token)
    #await client.send_message(bot_channel_info, output_message)

    print(output_message)


asyncio.run(main(chat_name=chat_input))
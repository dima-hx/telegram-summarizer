from datetime import datetime, time
from typing import List

async def get_chat_messages(client, entity, target_date):
    """
    Retrieve messages from a specified chat entity for a particular date
    
    Args:
        client: Telethon client instance
        entity: Chat entity to get messages from
        target_date: datetime.date object for which to get messages
        
    Returns:
        List of messages from the specified date
    """
    start_time = datetime.combine(datetime(2025, 1, 1).date(), time.min)
    end_time = datetime.combine(datetime(2025, 12, 31).date(), time.max)

    messages = []
    
    async for message in client.iter_messages(entity=entity, reverse=False):
        if message.date is None:
            continue
        
        msg_date = message.date.replace(tzinfo=None)
        
        if msg_date < start_time:
            continue
        
        if msg_date > end_time:
            break
        
        if start_time <= msg_date <= end_time:
            messages.append(message)
    
    return messages

async def format_history(messages):
    formatted = []
    for message in messages:
        if message.text:
            formatted.append(message.text)
    return "\n[DAY_DELIMITER]\n".join(formatted)

async def format_messages(messages):
    formatted = []
    
    for message in messages:
        # Get author name
        if message.sender:
            if hasattr(message.sender, 'first_name'):
                author = message.sender.first_name or message.sender.username or "Unknown"
            else:
                author = message.sender.title or "Unknown"
        else:
            author = "Unknown"
            
        # Get message text
        text = message.text or ""
        
        if text == "":  # Skip messages without text
            continue
        
        # Format reactions if present
        reactions_str = ""
        if message.reactions:
            reaction_counts = []
            for reaction in message.reactions.results:
                count = reaction.count
                emoji = reaction.reaction.emoticon
                reaction_counts.append(f"{emoji}:{count}")
            if reaction_counts:
                reactions_str = f" [Reactions: {', '.join(reaction_counts)}]"
                
        # Combine into final format
        formatted_msg = f"{author}: {text}{reactions_str}"
        formatted.append(formatted_msg)
        
    return "\n".join(formatted)

def get_output_message(key_topics: List[str], title: str, raw_messages_count: int, formatted_messages: str) -> str:
    """
    Generate the final output message with key topics, date, and message count
    
    Args:
        key_topics: List of key topics extracted from the chat history
        date: Date for which the chat history was summarized
        raw_messages_count: Total number of messages in the chat history
        
    Returns:
        Formatted output message
    """

    # calculate the time need to spend on reading the formatted_messages for the average reader (200 words per minute)
    words_per_minute = 200
    words_count = len(formatted_messages.split())
    reading_time = round(words_count / words_per_minute, 0)

    time_str = f", збережено ~{int(reading_time)} хв вашого часу" if reading_time > 2 else ""

    key_topics_str = "\n\n".join([f" - {topic}" for topic in key_topics])
    
    header = title if title else "Головне за минулий день"

    output_message = f"**{header}:**\n\n{key_topics_str}\n\n__За цей день було {raw_messages_count} повідомлень{time_str}__"
    return output_message
    
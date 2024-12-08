from datetime import datetime
import json
import openai
from typing import List

prompt = f"""Here is ONE day (yyyy-mm-dd [DATE]) of message history our local community chat.

Your key role is to briefly summarize the day's chat history in one message with some keypoints. 
Your choice of importance should be based on popularity of topic (discussions, comments, reactions), and on your personal belief that this is crucial for each residential member to know. 
Put the most important topics first.

Ignore advertisement. Ignore questions regarding some recommendations of services, products, etc. Ignore search for local services.
Ignore jokes. Do not provide information on what members did and how react.

Reply in Ukrainian language.
```
[CHAT_HISTORY]
```
"""

def summarize(open_ai_key: str, chat_history: str, date: datetime.date) -> tuple[List[str], str]:
    # Replace backticks and prepare prompt
    chat_history = chat_history.replace("`", "")
    prompt_with_history = prompt.replace("[CHAT_HISTORY]", chat_history).replace("[DATE]", date.strftime("%Y-%m-%d"))
    
    # Define function schema
    functions = [
        {
            "name": "process_summary",
            "description": "Process the chat summary and extract key topics",
            "parameters": {
                "type": "object",
                "properties": {
                    "key_topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of key topics discussed"
                    },
                    "title": {
                        "type": "string",
                        "description": "Title of the chat summary in ukrainian, e.g. 'Головне за 12 грудня' but you can choose your own"
                    }
                },
                "required": ["key_topics", "title"]
            }
        }
    ]

    # Make OpenAI API call
    client = openai.OpenAI(api_key=open_ai_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a chat summarizer that extracts key topics."},
            {"role": "user", "content": prompt_with_history}
        ],
        functions=functions,
        function_call={"name": "process_summary"}
    )

    # Parse and return the function call results
    function_args = json.loads(response.choices[0].message.function_call.arguments)
    return (function_args["key_topics"], function_args["title"])

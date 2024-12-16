from datetime import datetime
import json
import openai
from typing import List

prompt = f"""Here is ONE day (yyyy-mm-dd [DATE]) of message history in our local community chat.

Your key role is briefly summarizing the day's chat history with keypoints and summarized descriptions. Provide complete information for each key topic.
Your choice of importance should be based on the topic's importance, popularity (discussions, comments, reactions), and your personal belief that this is crucial for each residential member to know. 
Put the most important topics first.

Ignore advertisements or service recommendations. Ignore search for local services. Ignore jokes. Do not provide information on what members did and how they reacted.

Mark no more than one key point with an emoji icon at the beginning if it helps to highlight the importance of topic.

Do not hallucinate, do not mismatch topics. 

Reply in Ukrainian language without grammar mistakes.

```
[CHAT_HISTORY]
```

You can take into account the previous day's summary to avoid duplication of topics (or to continue them). Previous day's summary:
```
[PREVIOUS_DAY]
```
"""

def summarize(open_ai_key: str, chat_history: str, date: datetime.date, formatted_history: str) -> tuple[List[str], str]:
    # Replace backticks and prepare prompt
    chat_history = chat_history.replace("`", "")
    prompt_with_history = prompt.replace("[CHAT_HISTORY]", chat_history).replace("[DATE]", date.strftime("%Y-%m-%d"))
    prompt_with_history = prompt_with_history.replace("[PREVIOUS_DAY]", formatted_history)
    
    # Define function schema
    functions = [
        {
            "name": "process_summary",
            "description": "Process the chat and extract key topics with summary",
            "parameters": {
                "type": "object",
                "properties": {
                    "key_topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of topics discussed with their summaries"
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
            {"role": "system", "content": "You are a chat summarizer"},
            {"role": "user", "content": prompt_with_history}
        ],
        functions=functions,
        function_call={"name": "process_summary"},
    )

    # Parse and return the function call results
    try:
        function_args = json.loads(response.choices[0].message.function_call.arguments)
        key_topics = function_args.get("key_topics", [])
        title = function_args.get("title", "Summary")
    except (json.JSONDecodeError, KeyError, AttributeError) as e:
        print(f"Error parsing response: {e}")
        key_topics = []
        title = "Error in Summary"

    return (key_topics, title)

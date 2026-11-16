from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging

from config import KYIV, Config
from messages_functions import format_messages, get_chat_messages, get_output_message, format_history, DAILY_MARKER
from state import State
from summarizer import LLM, summarize

log = logging.getLogger(__name__)
HISTORY_DAYS = 7


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=KYIV)
    return start, start + timedelta(days=1)


async def already_posted_today(client, channel) -> bool:
    """A summary for yesterday is posted on the following day, so any summary posted today means we're done.
    Protects against double posts on restarts and while the old GitHub workflow still exists."""
    start, end = day_bounds(datetime.now(KYIV).date())
    posts = await get_chat_messages(client, channel, start, end)
    return any(DAILY_MARKER in (p.text or '') for p in posts)


def alerts_text_for(state: State | None, day: date) -> str:
    if not state:
        return ""
    lines = []
    for alert in state['alerts']:
        opened = datetime.fromtimestamp(alert['opened_at'], KYIV)
        if opened.date() == day and alert.get('live'):
            lines.append(f"{opened:%H:%M} {alert['topic']} ({alert.get('where') or '—'}), статус: {alert['status']}")
    return "\n".join(lines)


async def run_daily(client, cfg: Config, llm: LLM, state: State | None, target_date: date | None = None,
                    dry_run: bool = False) -> str | None:
    """Summarize target_date (default: yesterday in Kyiv) and post it to the channel. Returns the posted text."""
    chat = await client.get_entity(cfg.chat_name)
    channel = await client.get_entity(cfg.bot_channel)
    target_date = target_date or datetime.now(KYIV).date() - timedelta(days=1)

    if not dry_run and await already_posted_today(client, channel):
        log.info("Daily summary already posted today, skipping")
        if state:
            state['last_daily_date'] = target_date.isoformat()
            state.save()
        return None

    start_time, end_time = day_bounds(target_date)

    # summaries posted during the last week (each one covers the day before it was posted)
    previous = await get_chat_messages(client, channel, end_time - timedelta(days=HISTORY_DAYS), end_time)
    previous = [m for m in previous if DAILY_MARKER in (m.text or '')]
    formatted_history = await format_history(previous, KYIV)

    messages = await get_chat_messages(client, chat, start_time, end_time)
    messages.reverse()
    formatted_messages = await format_messages(messages)
    if not formatted_messages:
        log.warning("No messages for %s", target_date)
        if state:
            state['last_daily_date'] = target_date.isoformat()
            state.save()
        return None

    summary = summarize(llm, cfg.model, formatted_messages, target_date, formatted_history,
                        alerts_text_for(state, target_date))
    if not summary:
        raise RuntimeError(f"Empty summary for {target_date}")

    output = get_output_message(summary, len(messages), formatted_messages)
    if dry_run:
        return output

    await client.send_message(channel, output)
    if state:
        state['last_daily_date'] = target_date.isoformat()
        state.save()
    log.info("Posted summary for %s: %d messages, %d previous summaries", target_date, len(messages), len(previous))
    return output

"""
Urgent alerts: every few minutes a cheap model looks at fresh chat messages and decides whether
residents need to know something right now. Code-side gates keep it honest (distinct authors,
daily cap, dedupe), a stronger model writes the post, updates edit the same post.

Replay a past day without posting anything (uses the local session):
    python alerts.py --replay 2026-11-13
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from config import KYIV, Config, load_config, make_client
from state import State
from summarizer import LLM, triage, compose_alert, clean_note

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=30)        # context shown to the triage model
REPORTERS_SPAN = timedelta(minutes=20)  # distinct authors must report within this span
MIN_REPORTERS = 3
MAX_ALERTS_PER_DAY = 5
AUTO_CLOSE = timedelta(hours=6)
UPDATE_GAP = timedelta(minutes=30)    # min time between 'ongoing' updates of one alert ('resolved' always passes)
MAX_UPDATES = 6                       # per alert, excluding the final 'resolved'
KEEP_ALERTS = timedelta(days=2)       # resolved alerts stay in state for the next daily summary
NIGHT = (23, 7)                       # silent posts between 23:00 and 07:00


@dataclass
class Msg:
    id: int
    date: datetime
    sender_id: int | None
    text: str


def to_msg(message) -> Msg | None:
    if not message.text or message.date is None:
        return None
    return Msg(message.id, message.date, message.sender_id, message.text)


def format_window(msgs: list[Msg], new_ids: set[int]) -> str:
    labels: dict[int | None, str] = {}
    lines = []
    for m in msgs:
        label = labels.setdefault(m.sender_id, f"S{len(labels) + 1}")
        star = "*" if m.id in new_ids else ""
        text = m.text.replace("\n", " ")[:500]
        lines.append(f"{star}[#{m.id} {m.date.astimezone(KYIV):%H:%M} {label}] {text}")
    return "\n".join(lines)


def format_open_alerts(alerts: list[dict]) -> str:
    """Include what was already said, so the model doesn't repeat 'still no power' every check."""
    blocks = []
    for a in alerts:
        notes = "\n".join(f"   вже повідомлено: {n}" for n in a.get('notes', []))
        blocks.append(f"alert_id={a['id']}: {a['topic']} ({a.get('where') or '—'}), відкрито "
                      f"{datetime.fromtimestamp(a['opened_at'], KYIV):%H:%M}" + (f"\n{notes}" if notes else ""))
    return "\n".join(blocks)


def is_night(now: datetime) -> bool:
    h = now.astimezone(KYIV).hour
    return h >= NIGHT[0] or h < NIGHT[1]


def distinct_reporters(evidence: list, window: dict[int, Msg]) -> int:
    """Count distinct authors among the evidence, within REPORTERS_SPAN of the newest one. Never trust the LLM's count."""
    msgs = [window[i] for i in evidence if isinstance(i, int) and i in window and window[i].sender_id]
    if not msgs:
        return 0
    newest = max(m.date for m in msgs)
    return len({m.sender_id for m in msgs if newest - m.date <= REPORTERS_SPAN})


class AlertEngine:
    """
    Pure decision logic plus a `publish` callback, so the same code runs live (Telegram) and in replay (print).
    publish(kind, alert, text) -> message id (for kind == 'new') ; kind in {'new', 'edit'}
    """

    def __init__(self, cfg: Config, llm: LLM, state: State | dict, publish, compose=compose_alert):
        self.cfg, self.llm, self.state, self.publish, self.compose = cfg, llm, state, publish, compose

    def open_alerts(self) -> list[dict]:
        return [a for a in self.state['alerts'] if a['status'] in ('open', 'ongoing')]

    async def check(self, window_msgs: list[Msg], new_ids: set[int], now: datetime) -> None:
        self._housekeeping(now)
        if not new_ids:
            return

        window = {m.id: m for m in window_msgs}
        result = triage(self.llm, self.cfg.triage_model, f"{now.astimezone(KYIV):%Y-%m-%d %H:%M}",
                        format_open_alerts(self.open_alerts()), format_window(window_msgs, new_ids))

        for upd in result['updates']:
            await self._apply_update(upd, window, now)
        for new in result['new']:
            await self._maybe_create(new, window, new_ids, now)

    async def _maybe_create(self, new: dict, window: dict[int, Msg], new_ids: set[int], now: datetime):
        evidence = [i for i in new.get('evidence') or [] if i in window]
        topic = str(new.get('topic') or '').strip()
        reporters = distinct_reporters(evidence, window)
        day = now.astimezone(KYIV).date().isoformat()
        today_count = self.state['alerts_per_day'].get(day, 0)

        reject = None
        if not topic:
            reject = "no topic"
        elif not set(evidence) & new_ids:
            reject = "no fresh evidence"
        elif reporters < MIN_REPORTERS:
            reject = f"only {reporters} distinct reporters"
        elif today_count >= MAX_ALERTS_PER_DAY:
            reject = "daily cap reached"
        if reject:
            log.info("Alert candidate rejected (%s): %s", reject, topic)
            return

        evidence_text = format_window(sorted((window[i] for i in evidence), key=lambda m: m.id), set())
        text = self.compose(self.llm, self.cfg.model, topic, str(new.get('where') or ''), evidence_text)
        if not text:
            return

        alert = {
            'id': self.state['next_alert_id'], 'topic': topic, 'where': new.get('where') or '',
            'reason': new.get('reason') or '', 'text': text, 'status': 'open', 'live': self.cfg.alerts_live,
            'opened_at': now.timestamp(), 'updated_at': now.timestamp(), 'reporters': reporters,
        }
        self.state['next_alert_id'] += 1
        alert['msg_id'] = await self.publish('new', alert, text, evidence_text)
        self.state['alerts'].append(alert)
        self.state['alerts_per_day'][day] = today_count + 1
        log.info("Alert #%s posted (live=%s): %s", alert['id'], alert['live'], topic)

    async def _apply_update(self, upd: dict, window: dict[int, Msg], now: datetime):
        alert = next((a for a in self.open_alerts() if a['id'] == upd.get('alert_id')), None)
        note = clean_note(str(upd.get('note') or ''))
        if not alert or not note:
            return
        resolved = upd.get('status') == 'resolved'
        notes = alert.setdefault('notes', [])
        if not resolved and (len(notes) >= MAX_UPDATES or now.timestamp() - alert['updated_at'] < UPDATE_GAP.total_seconds()):
            log.info("Alert #%s update skipped (rate limit): %s", alert['id'], note)
            return
        icon = "✅" if resolved else "🔄"
        notes.append(note)
        alert['text'] += f"\n{icon} {now.astimezone(KYIV):%H:%M} — {note}"
        alert['status'] = 'resolved' if resolved else 'ongoing'
        alert['updated_at'] = now.timestamp()
        await self.publish('edit', alert, alert['text'], "")
        log.info("Alert #%s updated (%s): %s", alert['id'], alert['status'], note)

    def _housekeeping(self, now: datetime):
        ts = now.timestamp()
        for a in self.open_alerts():
            if ts - a['updated_at'] > AUTO_CLOSE.total_seconds():
                a['status'] = 'closed'
        self.state['alerts'] = [a for a in self.state['alerts'] if ts - a['opened_at'] < KEEP_ALERTS.total_seconds()]
        cutoff = (now.astimezone(KYIV) - KEEP_ALERTS).date().isoformat()
        self.state['alerts_per_day'] = {d: c for d, c in self.state['alerts_per_day'].items() if d >= cutoff}


# ---------------------------------------------------------------- live mode

class LiveAlerts:
    def __init__(self, client, cfg: Config, llm: LLM, state: State, chat, channel):
        self.client, self.cfg, self.state, self.chat, self.channel = client, cfg, state, chat, channel
        self.engine = AlertEngine(cfg, llm, state, self._publish)

    async def _publish(self, kind: str, alert: dict, text: str, evidence_text: str):
        now = datetime.now(KYIV)
        if not alert['live']:
            # shadow mode: show the decision to the owner, with the reasoning, in Saved Messages
            if kind == 'new':
                text = (f"🧪 ТЕСТОВЕ СПОВІЩЕННЯ #{alert['id']} (у канал не публікується)\n\n{text}\n\n"
                        f"Чому: {alert['reason']}\nАвторів: {alert['reporters']}\n\nДокази:\n{evidence_text[:1500]}")
                msg = await self.client.send_message('me', text, silent=is_night(now))
                return msg.id
            await self.client.send_message('me', f"🧪 Оновлення до #{alert['id']}:\n\n{text}", silent=True)
            return None
        if kind == 'new':
            msg = await self.client.send_message(self.channel, text, silent=is_night(now))
            return msg.id
        await self.client.edit_message(self.channel, alert['msg_id'], text)
        return None

    async def run_once(self):
        now = datetime.now(KYIV)
        last_seen = self.state['last_seen_msg_id']
        window_msgs: list[Msg] = []
        async for m in self.client.iter_messages(self.chat):
            if m.date < now - WINDOW:
                break
            msg = to_msg(m)
            if msg:
                window_msgs.append(msg)
        window_msgs.reverse()

        newest = max([m.id for m in window_msgs], default=last_seen)
        if last_seen == 0:
            # first start: don't alert about the past, just remember where we are
            self.state['last_seen_msg_id'] = newest
            self.state.save()
            return
        new_ids = {m.id for m in window_msgs if m.id > last_seen}

        await self.engine.check(window_msgs, new_ids, now)
        self.state['last_seen_msg_id'] = max(newest, last_seen)
        self.state.save()


# ---------------------------------------------------------------- replay

async def replay(day_str: str, interval_min: int, compose: bool):
    from daily import day_bounds
    from messages_functions import get_chat_messages
    from datetime import date

    cfg = load_config()
    cfg.alerts_live = False
    llm = LLM(cfg.open_ai_key)
    client = make_client(cfg)
    await client.start()
    chat = await client.get_entity(cfg.chat_name)
    start, end = day_bounds(date.fromisoformat(day_str))
    raw = await get_chat_messages(client, chat, start, end)
    await client.disconnect()
    msgs = sorted(filter(None, map(to_msg, raw)), key=lambda m: m.id)
    print(f"{day_str}: {len(msgs)} text messages, checking every {interval_min} min\n")

    state = {'alerts': [], 'alerts_per_day': {}, 'next_alert_id': 1}

    async def publish(kind, alert, text, evidence_text):
        t = datetime.fromtimestamp(alert['updated_at'], KYIV)
        if kind == 'new':
            print(f"===== {t:%H:%M} NEW #{alert['id']} ({alert['reporters']} authors) — {alert['reason']}\n{text}\n")
        else:
            print(f"----- {t:%H:%M} UPDATE #{alert['id']}: {text.splitlines()[-1]}\n")
        return alert['id']

    def placeholder(llm_, model, topic, where, evidence):
        return f"⚠️ **{topic}** ({where or '—'})"

    engine = AlertEngine(cfg, llm, state, publish, compose_alert if compose else placeholder)
    tick, last_id, calls = start, 0, 0
    while tick < end:
        tick += timedelta(minutes=interval_min)
        window = [m for m in msgs if tick - WINDOW <= m.date < tick]
        new_ids = {m.id for m in window if m.id > last_id}
        if new_ids:
            calls += 1
        await engine.check(window, new_ids, tick)
        last_id = max([m.id for m in window], default=last_id)

    print(f"Triage calls: {calls}, alerts: {len(state['alerts'])}, tokens: {llm.tokens}")


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    for noisy in ('telethon', 'httpx', 'openai'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser()
    p.add_argument('--replay', required=True, help='YYYY-MM-DD day to replay (nothing is posted)')
    p.add_argument('--interval', type=int, default=10)
    p.add_argument('--no-compose', action='store_true', help='skip the gpt-5.4 compose step to save tokens')
    a = p.parse_args()
    asyncio.run(replay(a.replay, a.interval, not a.no_compose))

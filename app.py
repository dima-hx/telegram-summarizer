"""
Azure App Service entrypoint (startup command: python app.py).

One long-running process:
  - tiny HTTP server on $PORT, because App Service restarts containers that don't answer and Always On pings "/"
  - single-runner lock in $STATE_DIR so two containers never share the Telegram session
  - scheduler: daily summary at DAILY_HOUR (with catch-up after restarts) + alert check every ALERT_INTERVAL_MIN
  - failures are reported to the owner's Saved Messages, once per error type per day
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta

from aiohttp import web

from alerts import LiveAlerts
from config import KYIV, load_config, make_client
from daily import run_daily
from state import State, RunnerLock
from summarizer import LLM

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
for noisy in ('telethon', 'httpx', 'openai', 'aiohttp.access'):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger('app')

DAILY_RETRY = timedelta(minutes=30)
DAILY_LATE_AFTER = timedelta(hours=2)  # warn the owner if the summary is still missing this long after DAILY_HOUR


class Service:
    def __init__(self):
        self.cfg = load_config()
        self.state = State(self.cfg.state_dir)
        self.lock = RunnerLock(self.cfg.state_dir)
        self.llm = LLM(self.cfg.open_ai_key)
        self.client = None
        self.status = 'starting'
        self.last_daily_attempt: datetime | None = None
        self.last_alert_check: datetime | None = None
        self.stop: asyncio.Event | None = None  # created inside the running loop (py3.9 binds Event to a loop)

    # ------------------------------------------------------------ health

    async def health(self, request):
        return web.json_response({
            'status': self.status,
            'last_daily_date': self.state['last_daily_date'],
            'last_alert_check': self.last_alert_check.isoformat() if self.last_alert_check else None,
            'alerts_live': self.cfg.alerts_live,
            'tokens_since_start': self.llm.tokens,
        })

    async def start_http(self):
        app = web.Application()
        app.router.add_get('/', self.health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '0.0.0.0', self.cfg.port).start()
        log.info("Health endpoint on :%s", self.cfg.port)

    # ------------------------------------------------------------ lock

    async def acquire_lock(self):
        while self.lock.held_by_other():
            self.status = 'waiting for lock'
            log.info("Another instance holds the runner lock, waiting")
            await asyncio.sleep(15)
        self.lock.heartbeat()
        log.info("Runner lock acquired")

    async def heartbeat_loop(self):
        while not self.stop.is_set():
            if not self.lock.still_mine():
                log.error("Runner lock taken by another instance, stopping")
                self.stop.set()
                return
            self.lock.heartbeat()
            await asyncio.sleep(30)

    # ------------------------------------------------------------ errors

    async def notify_error(self, key: str, text: str):
        log.error("%s: %s", key, text)
        today = datetime.now(KYIV).date().isoformat()
        if self.state['errors_notified'].get(key) == today:
            return
        self.state['errors_notified'][key] = today
        self.state.save()
        try:
            await self.client.send_message('me', f"🤖❗ ЖК-бот: {key}\n\n{text[-3000:]}")
        except Exception:
            log.exception("Could not notify owner")

    # ------------------------------------------------------------ jobs

    async def maybe_daily(self, now: datetime):
        yesterday = (now - timedelta(days=1)).date()
        due = now.replace(hour=self.cfg.daily_hour, minute=0, second=0, microsecond=0)
        if now < due or self.state['last_daily_date'] == yesterday.isoformat():
            return
        if self.last_daily_attempt and now - self.last_daily_attempt < DAILY_RETRY:
            return
        self.last_daily_attempt = now
        try:
            await run_daily(self.client, self.cfg, self.llm, self.state)
        except Exception:
            await self.notify_error('daily summary failed', traceback.format_exc())
            if now - due > DAILY_LATE_AFTER:
                await self.notify_error('daily summary missing', f"Підсумок за {yesterday} досі не опубліковано.")

    async def maybe_alerts(self, now: datetime, alerts: LiveAlerts):
        if self.last_alert_check and now - self.last_alert_check < timedelta(minutes=self.cfg.alert_interval_min):
            return
        self.last_alert_check = now
        try:
            await alerts.run_once()
        except Exception:
            await self.notify_error('alert check failed', traceback.format_exc())

    # ------------------------------------------------------------ main

    async def run(self):
        self.stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except NotImplementedError:  # Windows
                pass

        await self.start_http()
        await self.acquire_lock()
        heartbeat = asyncio.create_task(self.heartbeat_loop())

        self.client = make_client(self.cfg)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            self.status = 'telegram session is not authorized'
            log.critical("SESSION_VALUE is not authorized; run make_session.py and redeploy the setting")
            await self.stop.wait()
            return

        chat = await self.client.get_entity(self.cfg.chat_name)
        channel = await self.client.get_entity(self.cfg.bot_channel)
        alerts = LiveAlerts(self.client, self.cfg, self.llm, self.state, chat, channel)
        self.status = 'running'
        log.info("Connected. alerts_live=%s, daily at %02d:00, alerts every %s min",
                 self.cfg.alerts_live, self.cfg.daily_hour, self.cfg.alert_interval_min)

        try:
            while not self.stop.is_set():
                if not self.client.is_connected():
                    log.warning("Telegram disconnected, reconnecting")
                    await self.client.connect()
                now = datetime.now(KYIV)
                await self.maybe_daily(now)
                await self.maybe_alerts(now, alerts)
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.status = 'stopping'
            heartbeat.cancel()
            await self.client.disconnect()
            self.lock.release()
            log.info("Stopped")


if __name__ == '__main__':
    asyncio.run(Service().run())

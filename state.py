from __future__ import annotations

import json
import os
import time
import uuid

DEFAULT_STATE = {
    "last_daily_date": None,   # ISO date of the last day whose summary was posted
    "last_seen_msg_id": 0,     # newest chat message already triaged for alerts
    "alerts": [],              # open/recent alerts, see alerts.py
    "alerts_per_day": {},      # "YYYY-MM-DD" -> count of new alerts
    "errors_notified": {},     # "error_key" -> "YYYY-MM-DD" of last notification
    "next_alert_id": 1,
}


class State:
    """Small JSON state file. On Azure it lives in /home/data, which survives restarts and redeploys."""

    def __init__(self, state_dir: str):
        os.makedirs(state_dir, exist_ok=True)
        self.path = os.path.join(state_dir, 'state.json')
        self.data = dict(DEFAULT_STATE)
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as f:
                self.data.update(json.load(f))

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


class RunnerLock:
    """
    Only one process may use the Telegram session at a time: two connections with the same
    auth key from different IPs make Telegram revoke it (AuthKeyDuplicated). During a deploy
    App Service can briefly start the new container while the old one still runs, so the new
    one waits until the old heartbeat goes stale.
    """

    STALE_AFTER = 90  # seconds

    def __init__(self, state_dir: str):
        self.path = os.path.join(state_dir, 'runner.lock')
        self.owner = uuid.uuid4().hex

    def _read(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def held_by_other(self) -> bool:
        lock = self._read()
        return bool(lock) and lock['owner'] != self.owner and time.time() - lock['ts'] < self.STALE_AFTER

    def heartbeat(self):
        tmp = self.path + '.' + self.owner
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'owner': self.owner, 'ts': time.time()}, f)
        os.replace(tmp, self.path)

    def still_mine(self) -> bool:
        lock = self._read()
        return not lock or lock['owner'] == self.owner

    def release(self):
        if self.still_mine():
            try:
                os.remove(self.path)
            except OSError:
                pass

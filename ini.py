"""
Local CLI for the daily summary (the Azure service runs app.py).

    python ini.py --dry-run                     # print yesterday's summary
    python ini.py --dry-run --date 2026-11-13   # print the summary for a specific day
    python ini.py                               # post yesterday's summary (skips if already posted today)
"""
import argparse
import asyncio
import sys
from datetime import date

from config import load_config, make_client
from daily import run_daily
from summarizer import LLM


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='print instead of posting')
    parser.add_argument('--date', type=date.fromisoformat, help='day to summarize (default: yesterday)')
    args = parser.parse_args()

    cfg = load_config()
    llm = LLM(cfg.open_ai_key)
    client = make_client(cfg)
    await client.start()
    try:
        output = await run_daily(client, cfg, llm, state=None, target_date=args.date, dry_run=args.dry_run)
    finally:
        await client.disconnect()

    if output:
        sys.stdout.reconfigure(encoding='utf-8')
        print(output)
        print(f"\n[{len(output)} chars, tokens: {llm.tokens}]")


if __name__ == '__main__':
    asyncio.run(main())

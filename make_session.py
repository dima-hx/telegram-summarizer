"""
Create a dedicated Telegram login for the Azure service (run once, locally, in a terminal):

    python make_session.py          # QR login (default): scan with the Telegram app on your phone
    python make_session.py --code   # classic phone number + login code

QR: on your phone open Telegram > Settings > Devices > Link Desktop Device and scan the code.
The QR image opens automatically and refreshes every ~30 s until it is scanned. If you have a
cloud password (2FA), you'll be asked for it here in the terminal.

The session string is written to azure_session.txt (gitignored) and picked up by deploy.sh.
It is a separate login from dima.session, so local runs never clash with Azure.
Treat azure_session.txt like a password; revoke it any time in Telegram > Settings > Devices.
"""
import argparse
import asyncio
import getpass
import glob
import os
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from config import load_config

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'azure_session.txt')
QR_PNG = os.path.join(HERE, 'login_qr.png')


def show_qr(url: str, n: int):
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)
    # a new file per refresh: image viewers lock the previously opened one
    path = f"{QR_PNG[:-4]}_{n}.png"
    qrcode.make(url).save(path)
    print(f"QR #{n} saved to {path}")
    if sys.platform == 'win32':
        os.startfile(path)


async def qr_login(client: TelegramClient):
    qr = await client.qr_login()
    n = 0
    while True:
        n += 1
        show_qr(qr.url, n)
        print("Scan it: Telegram on your phone > Settings > Devices > Link Desktop Device")
        try:
            await qr.wait(timeout=max(10, (qr.expires - qr.expires.now(qr.expires.tzinfo)).total_seconds()))
            return
        except asyncio.TimeoutError:
            print("\nQR expired, generating a new one...\n")
            await qr.recreate()
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("Telegram cloud password (2FA): "))
            return


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', action='store_true', help='log in with phone number + code instead of QR')
    args = parser.parse_args()

    cfg = load_config()
    client = TelegramClient(StringSession(), cfg.api_id, cfg.api_hash,
                            device_model='evromisto-azure', app_version='1.0')
    try:
        if args.code:
            await client.start()
        else:
            await client.connect()
            await qr_login(client)

        me = await client.get_me()
        with open(OUT, 'w', encoding='utf-8') as f:
            f.write(client.session.save())
        print(f"\nLogged in as {me.first_name}. Session saved to {OUT}")
    finally:
        await client.disconnect()
        for f in glob.glob(f"{QR_PNG[:-4]}_*.png"):
            try:
                os.remove(f)
            except OSError:  # still open in the viewer
                pass


if __name__ == '__main__':
    asyncio.run(main())

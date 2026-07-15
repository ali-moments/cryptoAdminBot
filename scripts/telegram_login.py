import os
import asyncio
from telethon import TelegramClient

from app.config.settings import settings


os.makedirs(settings.sessions_dir, exist_ok=True)

async def main():
    sessions = [
        {
            "name": "reader",
            "path": os.path.join(settings.sessions_dir, settings.reader_session),
            "id": settings.reader_api_id,
            "hash": settings.reader_api_hash,
        },
        {
            "name": "sender",
            "path": os.path.join(settings.sessions_dir, settings.sender_session),
            "id": settings.sender_api_id,
            "hash": settings.sender_api_hash,
        }
    ]
    for session in sessions:
        print(f"\n🔑 Logging in with session: {session['name']}")
        print(f"📁 Session will be saved in: {session['path']}.session\n")

        client = TelegramClient(session['path'], session["id"], session["hash"])

        await client.start()

        if await client.is_user_authorized():
            me = await client.get_me()
            print("✅ Login successful!")
            print(f"👤 Logged in as: {me.first_name} {me.last_name or ''} (@{me.username})")
            print(f"💾 Session file created: {session['path']}.session")
            await client.disconnect()
        else:
            print("❌ Something went wrong.")

if __name__ == "__main__":
    asyncio.run(main())

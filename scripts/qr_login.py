import asyncio
import qrcode
from telethon import TelegramClient
from telethon.sessions import StringSession
import os


async def main(session_name, api_id, api_hash, save_session=False):
    # Create client
    client = TelegramClient(session_name, api_id, api_hash)

    print("Connecting to Telegram...")
    await client.connect()

    # Check if already logged in
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ Already logged in as {me.first_name} (@{me.username})")
        await client.disconnect()
        return

    print("🔄 Starting QR Code Login...")

    # Start QR login
    qr_login = await client.qr_login()

    # Generate QR Code
    print("\n📱 Scan this QR Code with your Telegram app (Settings → Devices → Scan QR):")

    # Create QR code image
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_login.url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save("telegram_qr.png")

    print("✅ QR Code saved as 'telegram_qr.png'")
    print(f"🔗 Or open this link manually: {qr_login.url}\n")

    # Show ASCII QR in terminal too
    try:
        qr.print_ascii()
    except Exception:
        pass

    print("⏳ Waiting for you to scan...")

    try:
        # Wait for scan + confirmation
        await qr_login.wait()

        # Check if 2FA password is required after scanning
        me = await client.get_me()
        if not me:
            # If wait() finishes but we aren't fully authorized, 2FA is likely needed
            raise Exception("Password required")

        print(f"✅ Login successful! Welcome {me.first_name} (@{me.username})")

    except asyncio.TimeoutError:
        print("⏰ QR Code expired. Trying again...")
        await qr_login.recreate()
        await main()  # Retry

    except Exception as e:
        # Catch the 2FA password requirement
        if "password" in str(e).lower() or "two-steps verification" in str(e).lower():
            print("\n🔒 Two-Step Verification is enabled.")
            import getpass
            # Securely prompt for password without showing it in the terminal
            pw = getpass.getpass("Enter your Telegram 2FA Password: ")

            try:
                # Provide the password to complete the sign-in
                me = await client.sign_in(password=pw)
                print(f"✅ Login successful after 2FA! Welcome {me.first_name} (@{me.username})")
            except Exception as sign_in_error:
                print(f"❌ Failed to sign in with password: {sign_in_error}")
                await client.disconnect()
                return
        else:
            print(f"❌ Error: {e}")
            await client.disconnect()
            return

    # === SAVE SESSION ===
    # This block executes if either login path succeeds
    if save_session:
        try:
            session_string = StringSession.save(client.session)
            with open(f"{session_name}_string.txt", "w") as f:
                f.write(session_string)
            print(f"💾 String session saved to {session_name}_string.txt")
        except Exception as session_error:
            print(f"⚠️ Could not save session string: {session_error}")

    await client.disconnect()
    os.remove("telegram_qr.png")

if __name__ == "__main__":
    # === YOUR CREDENTIALS ===
    # hand = input('do you wanna add another account?(y/n)')
    # if hand and hand.lower() in ['y', 'yes']:
    #     app_id = input("enter your app id: ")
    #     app_hash = input("enter your app hash: ")
    #     session_name = input("enter your session name: ")
    #     asyncio.run(main(session_name=session_name, api_id=app_id, api_hash=app_hash))
    # else:
    asyncio.run(main(
        session_name=os.path.join(os.environ["SESSIONS_DIR"], os.environ['READER_SESSION']),
        api_id=os.environ['READER_API_ID'],
        api_hash=os.environ['READER_API_HASH'],
    ))
    asyncio.run(main(
        session_name=os.path.join(os.environ["SESSIONS_DIR"], os.environ['SENDER_SESSION']),
        api_id=os.environ['SENDER_API_ID'],
        api_hash=os.environ['SENDER_API_HASH'],
    ))

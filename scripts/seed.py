import asyncio

from app.database.uow import UnitOfWork

SOURCES = [
    {
        "name": "BullsSignal",
        "telegram_username": "cheat_brawl_stars",
        "telegram_channel_id": -1001222691956,
        "parser_name": "bulls_signal2",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "BitcoinBullsVip",
        "telegram_channel_id": -1002051437532,
        "parser_name": "bitcoin_bulls_vip",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "GCRVVip",
        "telegram_channel_id": -1001895114921,
        "parser_name": "gcr_vvip",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "BitcoinBulls",
        "telegram_channel_id": -1001391574614,
        "parser_name": "bitcoin_bulls_vip",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "CryptoMermaids",
        "telegram_channel_id": -1002078531832,
        "telegram_username": "CryptoMermaids",
        "parser_name": "crypto_mermaids",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "CryptoMonk",
        "telegram_channel_id": -1001552004524,
        "telegram_username": "CryptoMonk_Japan",
        "parser_name": "crypto_monk",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "CryptoMonkPremium",
        "telegram_channel_id": -1001581833855,
        "parser_name": "crypto_monk",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "CryptoSafeCalls",
        "telegram_channel_id": -1001783301467,
        "telegram_username": "Crypto_Safe_Calls",
        "parser_name": "crypto_safe_calls",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "SpartaCrypto",
        "telegram_channel_id": -1002097370390,
        "telegram_username": "SpartaCrypto2",
        "parser_name": "sparta_crypto",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "CryptoAman",
        "telegram_channel_id": -1001884504990,
        "telegram_username": "cryptoamanvipfreemium",
        "parser_name": "crypto_aman",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "MaheeVIP",
        "telegram_channel_id": -1003872504487,
        "parser_name": "mahee_vip",
        "score": 100,
        "is_active": True,
    },
    {
        "name": "CryptoTradersVip",
        "telegram_channel_id": -1001823144300,
        "parser_name": "crypto_traders_vip",
        "score": 100,
        "is_active": True,
    },
]


async def main() -> None:
    async with UnitOfWork() as uow:
        for source in SOURCES:
            existing = await uow.signal_sources.get_by_channel_id(
                source["telegram_channel_id"],
            )

            if existing is not None:
                continue

            await uow.signal_sources.create(**source)

        await uow.commit()


if __name__ == "__main__":
    asyncio.run(main())

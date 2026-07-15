import asyncio

from app.database.uow import UnitOfWork


SOURCES = [
    {
        "name": "Bulls",
        "telegram_username": "cheat_brawl_stars",
        "telegram_channel_id": -1001222691956,  # TODO
        "parser_name": "bulls",
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

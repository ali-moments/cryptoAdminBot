import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import aiohttp
from websockets.asyncio.client import ClientConnection, connect
import orjson
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider
from app.config.settings import settings


class BinanceProvider(BaseProvider):
    #WS_URL = settings.binance_ws

    REST_URL = "https://fapi.binance.com/fapi/v1/ticker/price"

    def __init__(
        self,
        dispatcher,
    ) -> None:
        super().__init__(dispatcher)

        self._ws: ClientConnection | None = None
        self._session: aiohttp.ClientSession | None = None

        self._subscriptions: dict[str, int] = {}

        self._receive_task: asyncio.Task | None = None

        self._request_id = 0

    @property
    def name(
        self,
    ) -> Provider:
        return Provider.BINANCE

    async def connect(
        self,
    ) -> None:
        self._ws = await connect(settings.binance_ws)

        self._connected = True

        self._receive_task = asyncio.create_task(
            self._receive_loop(),
        )

    async def disconnect(
        self,
    ) -> None:
        self._connected = False

        if self._receive_task:
            self._receive_task.cancel()

        if self._ws:
            await self._ws.close()

        if self._session:
            await self._session.close()

    async def subscribe(
        self,
        symbol: str,
    ) -> None:
        if self._ws is None:
            raise RuntimeError("Provider is not connected.")

        symbol = symbol.lower()

        count = self._subscriptions.get(symbol, 0)

        if count == 0:
            self._request_id += 1

            await self._ws.send(
                orjson.dumps(
                    {
                        "method": "SUBSCRIBE",
                        "params": [
                            f"{symbol}@bookTicker",
                        ],
                        "id": self._request_id,
                    }
                )
            )

        self._subscriptions[symbol] = count + 1

    async def unsubscribe(
        self,
        symbol: str,
    ) -> None:
        if self._ws is None:
            return

        symbol = symbol.lower()

        count = self._subscriptions.get(symbol)

        if count is None:
            return

        if count == 1:
            self._request_id += 1

            await self._ws.send(
                orjson.dumps(
                    {
                        "method": "UNSUBSCRIBE",
                        "params": [
                            f"{symbol}@bookTicker",
                        ],
                        "id": self._request_id,
                    }
                )
            )

            del self._subscriptions[symbol]
            return

        self._subscriptions[symbol] = count - 1

    async def current_price(
        self,
        symbol: str,
    ) -> PriceTick | None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

        async with self._session.get(
            self.REST_URL,
            params={
                "symbol": symbol.upper(),
            },
        ) as response:
            response.raise_for_status()

            data = await response.json()

        return PriceTick(
            provider=self.name,
            symbol=data["symbol"],
            price=Decimal(data["price"]),
            timestamp=datetime.now(UTC),
        )

    async def _receive_loop(
        self,
    ) -> None:
        if self._ws is None:
            return

        try:
            async for message in self._ws:
                await self._handle_message(message)

        finally:
            self._connected = False

    async def _handle_message(
        self,
        message: str | bytes,
    ) -> None:
        data = orjson.loads(message)

        # Ignore subscribe/unsubscribe responses
        if "result" in data:
            return

        logger.trace(
            "Binance WS: {}",
            data,
        )

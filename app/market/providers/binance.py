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

        self.mark_connected()

        logger.info(
            "Binance websocket connected"
        )

        self._receive_task = asyncio.create_task(
            self._receive_loop(),
        )

    async def disconnect(
        self,
    ) -> None:
        self.mark_disconnected()

        if self._receive_task:
            self._receive_task.cancel()

            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

            self._receive_task = None

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

    async def subscribe(
        self,
        symbol: str,
    ) -> None:
        if self._ws is None or not self._connected:
            raise RuntimeError("Provider is not connected.")

        logger.info(
            "connected={}, ws={}",
            self._connected,
            self._ws,
        )

        symbol = symbol.lower()

        count = self._subscriptions.get(symbol, 0)

        if count == 0:
            self._request_id += 1

            payload = {
                "method": "SUBSCRIBE",
                "params": [
                    f"{symbol}@bookTicker",
                ],
                "id": self._request_id,
            }

            logger.trace(
                "Binance subscribe: {}",
                payload,
            )

            await self._ws.send(
                orjson.dumps(payload).decode(),
            )

            logger.info(
                "Subscribe request sent for {}",
                symbol,
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

            payload = {
                "method": "UNSUBSCRIBE",
                "params": [
                    f"{symbol}@bookTicker",
                ],
                "id": self._request_id,
            }

            logger.trace(
                "Binance unsubscribe: {}",
                payload,
            )

            await self._ws.send(
                orjson.dumps(payload).decode(),
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
                logger.trace(
                    "BINANCE_RAW_MESSAGE: {}",
                    message,
                )

                await self._handle_message(message)

        except asyncio.CancelledError:
            logger.debug(
                "Binance receive loop cancelled"
            )
            raise

        except Exception:
            logger.exception(
                "Binance websocket _receive_loop crashed"
            )

        finally:
            self.mark_disconnected()

            logger.warning(
                "Binance websocket disconnected"
            )

    async def _handle_message(
        self,
        message: str | bytes,
    ) -> None:
        try:
            data = orjson.loads(message)

            logger.trace(
                "BINANCE_PARSED_MESSAGE: {}",
                data,
            )

            # subscribe/unsubscribe ack
            if "result" in data:
                logger.debug(
                    "Binance subscription response: {}",
                    data,
                )
                return

            # Market data (bookTicker)
            logger.debug(f"BINANCE_TICKER_RECEIVED: {data.get('s', 'UNKNOWN')} - {data}")

            tick = PriceTick(
                provider=self.name,
                symbol=data["s"],
                price=Decimal(data["a"]),  # ask price for now
                timestamp=datetime.fromtimestamp(
                    data["E"] / 1000,
                    UTC,
                ),
            )

            await self._publish_price(tick)

            logger.info(
                "BINANCE_PRICE_UPDATE: {} @ {} (ts: {})",
                tick.symbol,
                tick.price,
                data["E"],
            )

        except Exception:
            logger.exception(
                "Failed to handle Binance message"
            )

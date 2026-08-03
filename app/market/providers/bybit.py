import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import aiohttp
from websockets.asyncio.client import ClientConnection, connect
import orjson
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.events import PriceUpdatedEvent
from app.market.providers.base import BaseProvider
from app.config.settings import settings


class BybitProvider(BaseProvider):
    REST_URL = "https://api.bybit.com/v5/market/tickers"

    def __init__(
        self,
        dispatcher,
    ) -> None:
        super().__init__(dispatcher)

        self._ws: ClientConnection | None = None
        self._session: aiohttp.ClientSession | None = None

        self._subscriptions: dict[str, int] = {}

        self._receive_task: asyncio.Task | None = None

    @property
    def name(
        self,
    ) -> Provider:
        return Provider.BYBIT

    async def connect(
        self,
    ) -> None:
        self._ws = await connect(settings.bybit_ws)

        self._connected = True

        logger.info(
            "Bybit websocket connected"
        )

        self._receive_task = asyncio.create_task(
            self._receive_loop(),
        )

    async def disconnect(
        self,
    ) -> None:
        self._connected = False

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

        symbol = symbol.upper()

        count = self._subscriptions.get(symbol, 0)

        if count == 0:
            payload = {
                "op": "subscribe",
                "args": [
                    f"tickers.{symbol}",
                ],
            }

            logger.trace(
                "Bybit subscribe: {}",
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

        symbol = symbol.upper()

        count = self._subscriptions.get(symbol)

        if count is None:
            return

        if count == 1:
            payload = {
                "op": "unsubscribe",
                "args": [
                    f"tickers.{symbol}",
                ],
            }

            logger.trace(
                "Bybit unsubscribe: {}",
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
                "category": "linear",
                "symbol": symbol.upper(),
            },
        ) as response:
            response.raise_for_status()

            data = await response.json()

        result = data["result"]["list"][0]

        return PriceTick(
            provider=self.name,
            symbol=result["symbol"],
            price=Decimal(result["lastPrice"]),
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

        except asyncio.CancelledError:
            logger.debug(
                "Bybit receive loop cancelled"
            )
            raise

        except Exception:
            logger.exception(
                "Bybit websocket _receive_loop crashed"
            )

        finally:
            self._connected = False

            logger.warning(
                "Bybit websocket disconnected"
            )

    async def _handle_message(
        self,
        message: str | bytes,
    ) -> None:
        try:
            data = orjson.loads(message)

            # subscribe/unsubscribe ack or ping
            if "op" in data:
                if data["op"] == "subscribe":
                    logger.debug(
                        "Bybit subscription response: {}",
                        data,
                    )
                elif data["op"] == "ping":
                    # respond to ping
                    if self._ws:
                        await self._ws.send(
                            orjson.dumps({"op": "pong"}).decode(),
                        )
                return

            # market data update
            if "topic" in data and data["topic"].startswith("tickers."):
                tick_data = data["data"]

                # Bybit sends "lastPrice" in snapshot and "price" in delta updates
                # Delta updates may only contain orderbook/volume fields without price
                price_str = tick_data.get("lastPrice") or tick_data.get("price")
                
                if not price_str:
                    # Delta update without price change - skip silently
                    return

                tick = PriceTick(
                    provider=self.name,
                    symbol=tick_data["symbol"],
                    price=Decimal(price_str),
                    timestamp=datetime.fromtimestamp(
                        int(data["ts"]) / 1000,
                        UTC,
                    ),
                )

                await self._dispatcher.publish(
                    PriceUpdatedEvent(
                        tick=tick,
                    )
                )

                logger.trace(
                    "Bybit market update: {}",
                    data,
                )

        except Exception:
            logger.exception(
                "Failed to handle Bybit message"
            )

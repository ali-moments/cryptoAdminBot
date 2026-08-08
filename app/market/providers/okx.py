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


class OKXProvider(BaseProvider):
    REST_URL = "https://www.okx.com/api/v5/market/ticker"

    def __init__(
        self,
        dispatcher,
    ) -> None:
        super().__init__(dispatcher)

        self._ws: ClientConnection | None = None
        self._session: aiohttp.ClientSession | None = None

        self._subscriptions: dict[str, int] = {}
        # Map normalized symbols to OKX instrument IDs
        self._symbol_map: dict[str, str] = {}

        self._receive_task: asyncio.Task | None = None

    @staticmethod
    def _normalize_to_okx(symbol: str) -> str:
        """
        Convert standard format (BTCUSDT) to OKX instrument ID (BTC-USDT-SWAP).
        For perpetual futures, we use the SWAP suffix.
        """
        symbol = symbol.upper()

        # If already in OKX format, return as-is
        if "-" in symbol:
            return symbol

        # Convert BTCUSDT -> BTC-USDT-SWAP
        if symbol.endswith("USDT"):
            base = symbol[:-4]  # Remove "USDT"
            return f"{base}-USDT-SWAP"
        elif symbol.endswith("USDC"):
            base = symbol[:-4]  # Remove "USDC"
            return f"{base}-USDC-SWAP"

        # Fallback: assume it's base-USDT
        return f"{symbol}-USDT-SWAP"

    @staticmethod
    def _normalize_from_okx(inst_id: str) -> str:
        """
        Convert OKX instrument ID (BTC-USDT-SWAP) to standard format (BTCUSDT).
        """
        # Remove -SWAP suffix and dashes
        parts = inst_id.split("-")
        if len(parts) >= 2:
            return f"{parts[0]}{parts[1]}"
        return inst_id

    @property
    def name(
        self,
    ) -> Provider:
        return Provider.OKX

    async def connect(
        self,
    ) -> None:
        self._ws = await connect(settings.okx_ws)

        self._connected = True

        logger.info(
            "OKX websocket connected"
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

        # Normalize to standard format for subscription tracking
        normalized_symbol = symbol.upper()
        # Convert to OKX format
        okx_symbol = self._normalize_to_okx(normalized_symbol)

        # Store the mapping
        self._symbol_map[normalized_symbol] = okx_symbol

        count = self._subscriptions.get(normalized_symbol, 0)

        if count == 0:
            payload = {
                "op": "subscribe",
                "args": [
                    {
                        "channel": "tickers",
                        "instId": okx_symbol,
                    }
                ],
            }

            logger.trace(
                "OKX subscribe: {}",
                payload,
            )

            await self._ws.send(
                orjson.dumps(payload).decode(),
            )

            logger.info(
                "Subscribe request sent for {} (OKX: {})",
                normalized_symbol,
                okx_symbol,
            )

        self._subscriptions[normalized_symbol] = count + 1

    async def unsubscribe(
        self,
        symbol: str,
    ) -> None:
        if self._ws is None:
            return

        normalized_symbol = symbol.upper()

        count = self._subscriptions.get(normalized_symbol)

        if count is None:
            return

        if count == 1:
            # Get OKX format from map
            okx_symbol = self._symbol_map.get(normalized_symbol, self._normalize_to_okx(normalized_symbol))

            payload = {
                "op": "unsubscribe",
                "args": [
                    {
                        "channel": "tickers",
                        "instId": okx_symbol,
                    }
                ],
            }

            logger.trace(
                "OKX unsubscribe: {}",
                payload,
            )

            await self._ws.send(
                orjson.dumps(payload).decode(),
            )

            del self._subscriptions[normalized_symbol]
            self._symbol_map.pop(normalized_symbol, None)
            return

        self._subscriptions[normalized_symbol] = count - 1

    async def current_price(
        self,
        symbol: str,
    ) -> PriceTick | None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

        normalized_symbol = symbol.upper()
        okx_symbol = self._normalize_to_okx(normalized_symbol)

        async with self._session.get(
            self.REST_URL,
            params={
                "instId": okx_symbol,
            },
        ) as response:
            response.raise_for_status()

            data = await response.json()

        result = data["data"][0]

        return PriceTick(
            provider=self.name,
            symbol=normalized_symbol,  # Return normalized symbol
            price=Decimal(result["last"]),
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
                "OKX receive loop cancelled"
            )
            raise

        except Exception:
            logger.exception(
                "OKX websocket _receive_loop crashed"
            )

        finally:
            self._connected = False

            logger.warning(
                "OKX websocket disconnected"
            )

    async def _handle_message(
        self,
        message: str | bytes,
    ) -> None:
        try:
            data = orjson.loads(message)

            # subscribe/unsubscribe ack or ping
            if "event" in data:
                if data["event"] == "subscribe":
                    logger.debug(
                        "OKX subscription response: {}",
                        data,
                    )
                elif data["event"] == "error":
                    logger.error(
                        "OKX subscription error: {}",
                        data,
                    )
                return

            # market data update
            if "arg" in data and data["arg"]["channel"] == "tickers":
                for tick_data in data["data"]:
                    # Normalize the symbol back to standard format
                    okx_inst_id = tick_data["instId"]
                    normalized_symbol = self._normalize_from_okx(okx_inst_id)

                    tick = PriceTick(
                        provider=self.name,
                        symbol=normalized_symbol,  # Use normalized symbol
                        price=Decimal(tick_data["last"]),
                        timestamp=datetime.fromtimestamp(
                            int(tick_data["ts"]) / 1000,
                            UTC,
                        ),
                    )

                    await self._dispatcher.publish(
                        PriceUpdatedEvent(
                            tick=tick,
                        )
                    )

                    # logger.trace(
                    #     "OKX market update: {} -> {}",
                    #     okx_inst_id,
                    #     normalized_symbol,
                    # )

        except Exception:
            logger.exception(
                "Failed to handle OKX message"
            )

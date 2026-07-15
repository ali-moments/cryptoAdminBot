from httpx import AsyncClient


class OurbitRegistry:
    EXCHANGE_INFO_URL = "https://api.ourbit.com/api/v3/exchangeInfo"

    def __init__(self) -> None:
        self._symbols: set[str] = set()

    async def refresh(self) -> None:
        async with AsyncClient() as client:
            response = await client.get(
                self.EXCHANGE_INFO_URL,
                timeout=15,
            )

            response.raise_for_status()

            data = response.json()

        self._symbols = {
            symbol["symbol"]
            for symbol in data["symbols"]
        }

    def contains(
        self,
        symbol: str,
    ) -> bool:
        return symbol in self._symbols

    @property
    def count(self) -> int:
        return len(self._symbols)

    def __contains__(
        self,
        symbol: str,
    ) -> bool:
        return self.contains(symbol)

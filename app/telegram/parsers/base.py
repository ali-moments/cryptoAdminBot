from abc import ABC, abstractmethod

from app.core.dto import ParsedSignal
from app.telegram.common.dto import RawTelegramMessage


class BaseParser(ABC):
    @abstractmethod
    async def parse(
        self,
        message: RawTelegramMessage,
    ) -> ParsedSignal | None:
        raise NotImplementedError

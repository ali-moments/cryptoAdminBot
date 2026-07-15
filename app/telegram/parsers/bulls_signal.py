from decimal import Decimal
import re
from loguru import logger
from app.core.dto import ParsedSignal, ParsedEntry, ParsedTarget
from app.database.enums import Direction
from app.telegram.common.dto import RawTelegramMessage
from app.telegram.parsers.base import BaseParser


class BullsSignalParser(BaseParser):
    async def parse(
        self,
        message: RawTelegramMessage,
    ) -> ParsedSignal | None:

        text = message.text

        if not text:
            return None

        if "🇧 🇺 🇱 🇱 🇸" not in text:
            return None

        try:
            direction_match = re.search(
                r"\(\s*(LONG|SHORT)",
                text,
                re.IGNORECASE,
            )

            if direction_match is None:
                return None

            symbol_match = re.search(
                r"([A-Z0-9]+/USDT)",
                text,
            )

            if symbol_match is None:
                return None

            leverage_match = re.search(
                r"Isolated\s*\((\d+)X\)",
                text,
                re.IGNORECASE,
            )

            entry_match = re.search(
                r"Entry Targets:\s*1\)\s*([\d.]+)",
                text,
            )

            if entry_match is None:
                return None

            target_matches = re.findall(
                r"Tp\s*\d+\)\s*([\d.]+)",
                text,
            )

            if not target_matches:
                return None

            sl_match = re.search(
                r"Sl\s*-\s*([\d.]+)",
                text,
            )

            if sl_match is None:
                return None

            return ParsedSignal(
                symbol=symbol_match.group(1).replace('/', ''),
                direction=Direction(direction_match.group(1).upper()),
                leverage=int(
                    leverage_match.group(1)
                ) if leverage_match else 15,
                entries=[
                    ParsedEntry(
                        position=1,
                        price=Decimal(entry_match.group(1)),
                    ),
                ],
                targets=[
                    ParsedTarget(
                        position=i,
                        price=Decimal(tp),
                    )
                    for i, tp in enumerate(target_matches, start=1)
                ],
                stop_loss=Decimal(sl_match.group(1)),
            )

        except Exception as e:
            logger.error("error parsing signal: {}", e)

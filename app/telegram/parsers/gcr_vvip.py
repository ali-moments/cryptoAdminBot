from decimal import Decimal
import re
from loguru import logger
from app.core.dto import ParsedSignal, ParsedEntry, ParsedTarget
from app.database.enums import Direction
from app.telegram.common.dto import RawTelegramMessage
from app.telegram.parsers.base import BaseParser


class GCRParser(BaseParser):
    async def parse(
        self,
        message: RawTelegramMessage,
    ) -> ParsedSignal | None:

        text = message.text

        if not text:
            return None

        if "Position:" not in text or "Entries:" not in text:
            return None

        try:
            direction_match = re.search(
                r"Position:\s*(LONG|SHORT)",
                text,
                re.IGNORECASE,
            )

            if direction_match is None:
                return None

            symbol_match = re.search(
                r"Coin\s*#([A-Z0-9]+/USDT)",
                text,
            )

            if symbol_match is None:
                return None

            leverage_match = re.search(
                r"Leverage:\s*Cross\s*(\d+)×",
                text,
                re.IGNORECASE,
            )

            entries_line_match = re.search(
                r"Entries:\s*(.+)",
                text,
            )

            if entries_line_match is None:
                return None

            entry_matches = re.findall(
                r"[\d.]+",
                entries_line_match.group(1),
            )

            if not entry_matches:
                return None

            targets_line_match = re.search(
                r"Targets:\s*🎯\s*(.+)",
                text,
            )

            if targets_line_match is None:
                return None

            target_matches = re.findall(
                r"[\d.]+",
                targets_line_match.group(1),
            )

            if not target_matches:
                return None

            sl_match = re.search(
                r"Stop Loss:\s*([\d.]+)",
                text,
                re.IGNORECASE,
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
                        position=i,
                        price=Decimal(price),
                    )
                    for i, price in enumerate(entry_matches, start=1)
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
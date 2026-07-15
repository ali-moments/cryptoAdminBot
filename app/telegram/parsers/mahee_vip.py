from decimal import Decimal
import re
from loguru import logger
from app.core.dto import ParsedSignal, ParsedEntry, ParsedTarget
from app.database.enums import Direction
from app.telegram.common.dto import RawTelegramMessage
from app.telegram.parsers.base import BaseParser


class MaheeVIPParser(BaseParser):
    async def parse(
        self,
        message: RawTelegramMessage,
    ) -> ParsedSignal | None:

        text = message.text

        if not text:
            return None

        if "@Mahee_Admin" not in text:
            return None

        try:
            direction_match = re.search(
                r"\((\d+)x(LONG|SHORT)\)",
                text,
                re.IGNORECASE,
            )

            if direction_match is None:
                return None

            symbol_match = re.search(
                r"#([A-Z0-9]+/USDT)",
                text,
            )

            if symbol_match is None:
                return None

            entry_line_match = re.search(
                r"ENTRY:\s*(.+)",
                text,
            )

            if entry_line_match is None:
                return None

            entry_matches = re.findall(
                r"\d+\.?\d*",
                entry_line_match.group(1),
            )

            if not entry_matches:
                return None

            targets_line_match = re.search(
                r"TARGETS:-\s*(.+)",
                text,
            )

            if targets_line_match is None:
                return None

            target_matches = re.findall(
                r"\d+\.?\d*",
                targets_line_match.group(1),
            )

            if not target_matches:
                return None

            sl_match = re.search(
                r"STOP LOSS:\s*([\d.]+)",
                text,
                re.IGNORECASE,
            )

            if sl_match is None:
                return None

            return ParsedSignal(
                symbol=symbol_match.group(1).replace('/', ''),
                direction=Direction(direction_match.group(2).upper()),
                leverage=int(direction_match.group(1)),
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

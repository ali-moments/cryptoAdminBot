from decimal import Decimal
import re
from loguru import logger
from app.core.dto import ParsedSignal, ParsedEntry, ParsedTarget
from app.database.enums import Direction
from app.telegram.common.dto import RawTelegramMessage
from app.telegram.parsers.base import BaseParser


class BullsSignal2Parser(BaseParser):
    async def parse(
        self,
        message: RawTelegramMessage,
    ) -> ParsedSignal | None:

        text = message.text

        if not text:
            return None

        if "Signal Strategy" not in text or "Strategy Details" not in text:
            return None

        try:
            direction_line_match = re.search(
                r"(Short|Long)\s*:\s*(.+)",
                text,
                re.IGNORECASE,
            )

            if direction_line_match is None:
                return None

            symbol_match = re.search(
                r"#([A-Z0-9]+USDT)",
                text,
            )

            if symbol_match is None:
                return None

            leverage_match = re.search(
                r"Leverage:\s*(\d+)x",
                text,
                re.IGNORECASE,
            )

            entry_matches = re.findall(
                r"[\d.]+",
                direction_line_match.group(2),
            )

            if not entry_matches:
                return None

            target_matches = re.findall(
                r"TP\s*\d+:\s*([\d.]+)",
                text,
                re.IGNORECASE,
            )

            if not target_matches:
                return None

            sl_match = re.search(
                r"Stop-Loss:\s*([\d.]+)",
                text,
                re.IGNORECASE,
            )

            if sl_match is None:
                return None

            return ParsedSignal(
                symbol=symbol_match.group(1),
                direction=Direction(direction_line_match.group(1).upper()),
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

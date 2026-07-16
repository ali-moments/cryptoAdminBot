from loguru import logger
from app.core.dto import ParsedSignal, ValidatedSignal
from app.database.enums import Direction
from app.database.uow import UnitOfWork
from app.market.symbol_registry import OurbitRegistry


class ValidationService:
    def __init__(
        self,
        registry: OurbitRegistry,
    ) -> None:
        self._registry = registry

    async def validate(
        self,
        signal: ParsedSignal,
        uow: UnitOfWork,
    ) -> ValidatedSignal | None:
        if not self._validate_structure(signal):
            logger.trace("signal structure is not valid.")
            return None

        if not self._validate_prices(signal):
            logger.trace("signal prices is not valid.")
            return None

        if not self._registry.contains(signal.symbol):
            logger.trace("Symbol is not in Ourbit Registry!")
            return None

        return ValidatedSignal(
            symbol=signal.symbol,
            direction=signal.direction,
            leverage=signal.leverage,
            entries=signal.entries,
            targets=signal.targets,
            stop_loss=signal.stop_loss,
        )

    def _validate_structure(
        self,
        signal: ParsedSignal,
    ) -> bool:
        if signal.leverage < 1 or signal.leverage > 100:
            return False

        if not signal.entries:
            return False

        if not signal.targets:
            return False

        entries = [entry.price for entry in signal.entries]
        targets = [target.price for target in signal.targets]

        if len(set(entries)) != len(entries):
            return False

        if len(set(targets)) != len(targets):
            return False

        if any(price <= 0 for price in entries):
            return False

        if any(price <= 0 for price in targets):
            return False

        if signal.stop_loss <= 0:
            return False

        return True

    def _validate_prices(
        self,
        signal: ParsedSignal,
    ) -> bool:
        entries = [entry.price for entry in signal.entries]
        targets = [target.price for target in signal.targets]
        sl = signal.stop_loss

        ascending = all(
            previous < current
            for previous, current in zip(
                targets,
                targets[1:],
            )
        )

        descending = all(
            previous > current
            for previous, current in zip(
                targets,
                targets[1:],
            )
        )

        if signal.direction is Direction.LONG:
            if not ascending:
                return False

            if sl >= min(entries):
                return False

            if min(targets) <= max(entries):
                return False

        else:
            if not descending:
                return False

            if sl <= max(entries):
                return False

            if max(targets) >= min(entries):
                return False

        return True

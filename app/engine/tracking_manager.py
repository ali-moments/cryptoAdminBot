import asyncio
from collections import defaultdict

from app.database.uow import UnitOfWork
from app.engine.tracker import Tracker
from app.market.cache import PriceCache
from app.engine.action_processor import ActionProcessor


class TrackingManager:
    """Manages tracking lifecycle and owns transaction boundaries.
    
    Polls active trackings every interval, processes them within a single
    transaction to ensure all state changes are persisted atomically.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        tracker: Tracker,
        processor: ActionProcessor,
        cache: PriceCache,
        interval: float = 2.0,
    ) -> None:
        self._uow = uow
        self._tracker = tracker
        self._processor = processor
        self._cache = cache

        self._interval = interval

        self._task: asyncio.Task | None = None

    async def start(
        self,
    ) -> None:
        if self._task is not None:
            return

        self._task = asyncio.create_task(
            self._run(),
        )

    async def stop(
        self,
    ) -> None:
        if self._task is None:
            return

        self._task.cancel()

        try:
            await self._task
        except asyncio.CancelledError:
            pass

        self._task = None

    async def _run(
        self,
    ) -> None:
        while True:
            await self._tick()

            await asyncio.sleep(
                self._interval,
            )

    async def _tick(
        self,
    ) -> None:
        """Process all active trackings within a single transaction.
        
        This method owns the transaction lifecycle:
        - Opens UnitOfWork
        - Loads trackings (objects attached to session)
        - Tracker updates state (tracked by SQLAlchemy)
        - ActionProcessor processes actions (mutates attached objects)
        - Commits all changes atomically
        """
        async with self._uow:
            # Load all active trackings - objects are attached to session
            trackings = await self._uow.trackings.get_active()

            # Group by symbol for efficient tick lookup
            grouped: dict[str, list] = defaultdict(list)
            for tracking in trackings:
                grouped[tracking.signal.symbol].append(tracking)

            # Process each symbol's trackings
            for symbol, symbol_trackings in grouped.items():
                tick = self._cache.get(symbol)

                if tick is None:
                    continue

                # Process each tracking
                for tracking in symbol_trackings:
                    # Tracker updates runtime state (peak price, halfway flag)
                    # These modifications are tracked by SQLAlchemy
                    actions = await self._tracker.track(
                        tracking=tracking,
                        tick=tick,
                    )

                    if not actions:
                        continue

                    # ActionProcessor processes actions
                    # Mutates the same tracking object
                    await self._processor.process(
                        tracking,
                        actions,
                    )

            # Commit all changes atomically
            # - Tracker state updates
            # - ActionProcessor action processing
            # - All persisted together
            await self._uow.commit()

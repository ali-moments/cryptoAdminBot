import asyncio
from collections import defaultdict
from collections.abc import Callable

from loguru import logger
from app.database.uow import UnitOfWork
from app.database.enums import TrackingStatus
from app.engine.tracker import Tracker
from app.market.cache import PriceCache
from app.engine.action_processor import ActionProcessor
from app.market.events import ProviderChangedEvent


class TrackingManager:
    """Manages tracking lifecycle and owns transaction boundaries.

    Polls active trackings every interval, processes them within a single
    transaction to ensure all state changes are persisted atomically.
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        tracker: Tracker,
        processor: ActionProcessor,
        cache: PriceCache,
        interval: float = 2.0,
    ) -> None:
        self._uow_factory = uow_factory
        self._tracker = tracker
        self._processor = processor
        self._cache = cache

        self._interval = interval

        self._task: asyncio.Task | None = None

        # Runtime state: tracks which trackings have been initialized
        # in THIS engine session. Reset on restart.
        self._initialized_trackings: set[int] = set()

    def reset_initialization_state(self) -> None:
        """Reset initialization state to force re-initialization of all trackings.

        Called after provider recovery to ensure trackings resume processing
        even if they were already initialized before the provider failure.
        
        Note: This is intentionally broad - it resets ALL trackings rather than
        trying to determine which ones were affected by the provider change.
        This ensures robustness at the cost of some redundant reinitialization.
        """
        count = len(self._initialized_trackings)
        self._initialized_trackings.clear()
        logger.info(f"Reset initialization state for {count} trackings - will re-initialize on next tick")

    async def on_provider_changed(self, event: ProviderChangedEvent) -> None:
        """Handle provider change events by resetting tracking initialization state.
        
        Currently resets ALL tracking initialization state as a conservative approach.
        This ensures all trackings are re-evaluated after provider changes, which
        prevents missed actions but may cause some redundant reinitialization.
        
        The alternative would be to track which provider each tracking uses and only
        reset those affected by the change, but this adds complexity without clear
        benefit since reinitialization is designed to be idempotent.
        """
        logger.info(f"Provider changed from {event.previous.value} to {event.current.value} - resetting tracking initialization")
        self.reset_initialization_state()

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
            try:
                await self._tick()
            except asyncio.CancelledError:
                # Preserve shutdown behavior
                raise
            except Exception:
                # Log error and continue to next tick
                # This prevents single failures from stopping the engine
                logger.exception("Tick failed, continuing to next tick")

            await asyncio.sleep(
                self._interval,
            )

    async def _tick(
        self,
    ) -> None:
        """Process all active trackings within a single transaction.

        For each tracking:
        1. Initialization phase (if not yet initialized this session)
           - Delegates to existing entry rules for detection
           - If actions emitted: process and skip normal rules this cycle
           - If no actions: continue to normal rules
        2. Normal rule pipeline (if initialization didn't emit actions)

        This ensures one clear state transition per engine cycle.
        """
        async with self._uow_factory() as uow:
            # Load all active trackings - objects are attached to session
            trackings = await uow.trackings.get_active()

            # Group by symbol for efficient tick lookup
            grouped: dict[str, list] = defaultdict(list)
            for tracking in trackings:
                grouped[tracking.signal.symbol].append(tracking)

            # Process each symbol's trackings
            for symbol, symbol_trackings in grouped.items():
                tick = self._cache.get(symbol)

                if tick is None:
                    logger.trace(f"No market data available for {symbol}, skipping processing")
                    continue

                # Process each tracking
                for tracking in symbol_trackings:
                    # ================================================
                    # INITIALIZATION PHASE
                    # ================================================
                    # Check if this tracking needs initialization
                    if tracking.id not in self._initialized_trackings:
                        # First observation in this engine session
                        logger.info(f"TRACKING STARTED: {symbol} (tracking_id={tracking.id}, status={tracking.status.value})")

                        # Delegate to existing entry rules for detection
                        init_actions = await self._initialize_tracking(tracking, tick)

                        # Mark as initialized for this session
                        self._initialized_trackings.add(tracking.id)

                        if init_actions:
                            # Initialization emitted actions (state transition)
                            # Process them and skip normal rules THIS cycle
                            logger.debug(f"Processing {len(init_actions)} initialization actions for {symbol}")
                            await self._processor.process(tracking, init_actions, uow)
                            continue  # Move to next tracking

                        # Initialization emitted nothing
                        # Fall through to normal rules
                    # else:
                    #     # Tracking already initialized in this session
                    #     logger.trace(f"TRACKING ACTIVE: {symbol} (tracking_id={tracking.id}, status={tracking.status.value})")

                    # ================================================
                    # NORMAL PROCESSING
                    # ================================================
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
                        uow,
                    )

            # Commit all changes atomically
            await uow.commit()

    async def _initialize_tracking(self, tracking, tick):
        """Perform startup observation for this tracking.

        This is the FIRST market observation for this tracking in the
        current engine session.

        Business Rule: Initialization must allow missed TP recovery.
        If price moved beyond unprocessed targets while engine was offline,
        those targets must be recovered during normal rule processing.

        For WAITING_ENTRY trackings: Delegate to EntryRule to detect entries.
        For TRACKING trackings: Return [] to allow normal rule processing.

        This method has NO side effects. It only observes and returns actions.

        Runs once per tracking per engine session.
        After restart, runs again for all active trackings.
        """

        # Only handle entry detection for trackings waiting for entry
        if tracking.status != TrackingStatus.WAITING_ENTRY:
            # For trackings already in TRACKING state, return [] to allow
            # normal processing to handle TP recovery
            return []

        # If position already entered, no initialization needed
        if tracking.entry1_touched:
            return []

        # Delegate to EntryRule for entry detection
        # This uses the exact same logic as normal processing,
        # ensuring consistency between initialization and live processing
        entry_rule = self._tracker._entry

        # Get ordered entries for the rule
        first_entry, second_entry = self._tracker._get_ordered_entries(tracking.signal)

        # Use EntryRule's logic to detect entries
        # This handles all scenarios:
        # - Single entry detection
        # - Gap detection (both entries crossed)
        # - Emergency entry
        # - TP1 crossing protection
        actions = await entry_rule._handle_waiting_entry(
            tracking=tracking,
            current_price=tick.price,
            current_time=tick.timestamp,
            first_entry=first_entry,
        )

        return actions

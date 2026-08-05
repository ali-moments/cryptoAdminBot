import asyncio
from collections import defaultdict

from app.database.uow import UnitOfWork
from app.database.enums import TrackingStatus
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
        
        # Runtime state: tracks which trackings have been initialized
        # in THIS engine session. Reset on restart.
        self._initialized_trackings: set[int] = set()

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
                    # ================================================
                    # INITIALIZATION PHASE
                    # ================================================
                    # Check if this tracking needs initialization
                    if tracking.id not in self._initialized_trackings:
                        # First observation in this engine session
                        # Delegate to existing entry rules for detection
                        init_actions = await self._initialize_tracking(tracking, tick)
                        
                        # Mark as initialized for this session
                        self._initialized_trackings.add(tracking.id)
                        
                        if init_actions:
                            # Initialization emitted actions (state transition)
                            # Process them and skip normal rules THIS cycle
                            await self._processor.process(tracking, init_actions)
                            continue  # Move to next tracking
                        
                        # Initialization emitted nothing
                        # Fall through to normal rules
                    
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
                    )

            # Commit all changes atomically
            await self._uow.commit()
    
    async def _initialize_tracking(self, tracking, tick):
        """Perform startup observation for this tracking.
        
        This is the FIRST market observation for this tracking in the
        current engine session.
        
        Delegates to the existing EntryRule logic to avoid duplicating
        business rules. This ensures there is only one source of truth
        for entry detection.
        
        Business Rule: If price is already beyond entry points when
        engine first observes the tracking, entry actions are emitted.
        
        This method has NO side effects. It only observes and returns actions.
        
        Runs once per tracking per engine session.
        After restart, runs again for all active trackings.
        """
        
        # Only initialize trackings waiting for entry
        if tracking.status != TrackingStatus.WAITING_ENTRY:
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

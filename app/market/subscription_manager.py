import asyncio
from collections.abc import Callable
from loguru import logger

from app.database.uow import UnitOfWork


class SubscriptionManager:
    """Synchronizes ProviderManager subscriptions with active trackings from database.

    Responsibilities:
    - Load active trackings from database periodically
    - Extract unique symbols that should be subscribed
    - Call ProviderManager.sync() to synchronize subscriptions
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        provider_manager,  # Import would cause circular dependency
        interval: float = 5.0,
    ) -> None:
        self._uow_factory = uow_factory
        self._provider_manager = provider_manager
        self._interval = interval

        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the subscription synchronization loop."""
        if self._running:
            logger.warning("SubscriptionManager already running")
            return

        self._running = True
        logger.info(f"Starting SubscriptionManager with {self._interval}s interval")

        self._task = asyncio.create_task(self._run())

        logger.success("SubscriptionManager started")

    async def stop(self) -> None:
        """Stop the subscription synchronization loop."""
        if not self._running:
            return

        logger.info("Stopping SubscriptionManager")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("SubscriptionManager stopped")

    async def _run(self) -> None:
        """Main synchronization loop."""
        while self._running:
            try:
                await self._sync_subscriptions()
            except asyncio.CancelledError:
                # Preserve shutdown behavior
                break
            except Exception:
                # Log error and continue to next cycle
                # This prevents single failures from stopping synchronization
                logger.exception("Subscription sync failed, continuing to next cycle")

            # Wait for next cycle
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def _sync_subscriptions(self) -> None:
        """Load active trackings and synchronize subscriptions."""
        try:
            # Load active trackings from database
            async with self._uow_factory() as uow:
                trackings = await uow.trackings.get_active()

            # Extract unique symbols that should be subscribed
            required_symbols = {tracking.signal.symbol for tracking in trackings}

            # Log what we found
            if required_symbols:
                logger.trace(f"SUBSCRIPTION SYNC: Found {len(trackings)} active trackings requiring {len(required_symbols)} unique symbols: {sorted(required_symbols)}")
            else:
                logger.trace("SUBSCRIPTION SYNC: No active trackings found, no subscriptions required")

            # Delegate to ProviderManager for subscription synchronization
            await self._provider_manager.sync(required_symbols)

        except Exception:
            # Re-raise to be caught by _run() loop
            raise

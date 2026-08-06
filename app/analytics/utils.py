from decimal import Decimal
from app.repositories.signal import SignalRepository
from app.repositories.tracking import TrackingRepository
from app.repositories.telegram import TelegramRepository


def calculate_pnl(
        signal_id: int, tracking_id: int|None=None, tp_number: int|None=None
    ) -> Decimal:
    pass

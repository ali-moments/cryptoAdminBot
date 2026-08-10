from app.config.settings import settings

class States:
    def __init__(self) -> None:
        self.target_channel = settings.test_channel
        self.dev_mode = True
        self.emergency_entry_timeout = 3
        self.signal_entry_timeout = 2
        self.signal_expiry_timeout = 72
        self.active_signals_limit = 7

    def set_dev_mode(self) -> bool:
        self.dev_mode = not self.dev_mode
        if self.dev_mode:
            self.target_channel = settings.test_channel
        else:
            self.target_channel = settings.royal_channel

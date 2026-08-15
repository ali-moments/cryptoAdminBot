from app.config.settings import settings

class States:
    def __init__(self) -> None:
        self.target_channel = settings.test_channel
        self.dev_mode = True
        self.bot_enabled = True  # Global bot ON/OFF state
        self.emergency_entry_timeout = 3
        self.signal_entry_timeout = 2
        self.signal_expiry_timeout = 72
        self.active_signals_limit = 5

    def toggle_dev_mode(self) -> bool:
        self.dev_mode = not self.dev_mode
        if self.dev_mode:
            self.target_channel = settings.test_channel
        else:
            self.target_channel = settings.royal_channel
        return self.dev_mode

    def toggle_bot_enabled(self) -> bool:
        """Toggle global bot state and return new state."""
        self.bot_enabled = not self.bot_enabled
        return self.bot_enabled

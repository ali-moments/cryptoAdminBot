"""Admin bot application setup using python-telegram-bot."""

from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from loguru import logger

from app.config.settings import settings
from app.services.admin import AdminService
from app.telegram.admin.handlers import (
    start_command,
    admin_command,
    status_command,
    callback_handler,
)


class AdminBot:
    """Admin bot using python-telegram-bot library."""

    def __init__(self, admin_service: AdminService) -> None:
        self.admin_service = admin_service
        self.application: Application | None = None
        
    async def start(self) -> None:
        """Start the admin bot."""
        if self.application:
            logger.warning("Admin bot already started")
            return
            
        logger.info("Starting admin bot...")
        
        # Create application
        self.application = Application.builder().token(settings.admin_bot_token).build()
        
        # Add handlers with admin_service injection
        self.application.add_handler(
            CommandHandler("start", lambda u, c: start_command(u, c, self.admin_service))
        )
        self.application.add_handler(
            CommandHandler("admin", lambda u, c: admin_command(u, c, self.admin_service))
        )
        self.application.add_handler(
            CommandHandler("status", lambda u, c: status_command(u, c, self.admin_service))
        )
        self.application.add_handler(
            CallbackQueryHandler(lambda u, c: callback_handler(u, c, self.admin_service))
        )
        
        # Initialize and start the application
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        logger.success("Admin bot started successfully")
        
    async def stop(self) -> None:
        """Stop the admin bot."""
        if not self.application:
            return
            
        logger.info("Stopping admin bot...")
        
        try:
            # Stop polling
            if self.application.updater.running:
                await self.application.updater.stop()
            
            # Stop application
            await self.application.stop()
            await self.application.shutdown()
            
            self.application = None
            logger.info("Admin bot stopped")
            
        except Exception as e:
            logger.error(f"Error stopping admin bot: {e}")
            
    async def is_running(self) -> bool:
        """Check if the admin bot is running."""
        return (
            self.application is not None 
            and hasattr(self.application, 'updater') 
            and self.application.updater.running
        )
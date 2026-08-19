"""Command and callback handlers for admin bot."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from loguru import logger

from app.config.settings import settings
from app.services.admin import AdminService
from app.telegram.admin.keyboards import (
    build_main_menu,
    build_sources_keyboard,
    build_trackings_keyboard,
    build_confirmation_keyboard,
    build_tracking_detail_keyboard,
    build_tp_selection_keyboard,
    build_entry_selection_keyboard,
)


async def safe_edit_message(query, text: str, reply_markup=None, parse_mode=None) -> bool:
    """Safely edit a message, handling 'Message is not modified' errors.

    Returns True if message was edited, False if no change needed.
    """
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Message content unchanged, skipping edit")
            return False
        # Re-raise other BadRequest errors
        raise
    except Exception:
        # Re-raise other exceptions
        raise


def is_admin(user_id: int) -> bool:
    """Check if user is in admin list."""
    return user_id in settings.admins


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService) -> None:
    """Handle /start command."""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Access denied. You are not authorized to use this bot.")
        logger.warning(f"Unauthorized access attempt from user {user_id}")
        return

    welcome_text = (
        "🤖 *Admin Bot*\n\n"
        "Welcome to the trading bot admin panel.\n"
        "Use /admin to access the control panel.\n\n"
        "Available commands:\n"
        "• /admin - Main control panel\n"
        "• /status - Quick status check"
    )

    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService) -> None:
    """Handle /admin command - show main menu."""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Access denied.")
        logger.warning(f"Unauthorized admin command attempt from user {user_id}")
        return

    await show_main_menu(update, context, admin_service)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService) -> None:
    """Handle /status command - quick status overview."""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Access denied.")
        return

    try:
        stats = await admin_service.get_system_stats()
        bot_status = admin_service.get_bot_status()

        status_text = (
            f"📊 *Bot Status*\n\n"
            f"🤖 Bot: {'🟢 Enabled' if bot_status['bot_enabled'] else '🔴 Disabled'}\n"
            f"⚙️ Mode: {'🟡 Development' if bot_status['dev_mode'] else '🟢 Production'}\n"
            f"📋 Sources: {stats['active_sources_count']}/{stats['total_sources_count']} active\n"
            f"🎯 Active Trackings: {stats['active_trackings_count']}\n"
            #f"📊 Signals Limit: {stats['signals_limit']}\n"
            f"📺 Target Channel: {bot_status['target_channel']}"
        )

        await update.message.reply_text(status_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error in status command: {e}")
        await update.message.reply_text("❌ Error retrieving status information.")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService) -> None:
    """Handle callback queries from inline keyboards."""
    query = update.callback_query
    user_id = query.from_user.id

    # Authorization check for callbacks
    if not is_admin(user_id):
        await query.answer("❌ Access denied.", show_alert=True)
        logger.warning(f"Unauthorized callback attempt from user {user_id}")
        return

    await query.answer()  # Acknowledge the callback

    data = query.data

    try:
        if data == "main_menu":
            await show_main_menu(update, context, admin_service, edit=True)

        elif data == "toggle_bot":
            await handle_toggle_bot(update, context, admin_service)

        elif data == "toggle_dev":
            await handle_toggle_dev(update, context, admin_service)

        elif data.startswith("sources:"):
            page = int(data.split(":")[1])
            await show_sources_page(update, context, admin_service, page, edit=True)

        elif data.startswith("trackings:"):
            page = int(data.split(":")[1])
            await show_trackings_page(update, context, admin_service, page, edit=True)

        elif data.startswith("src_toggle:"):
            source_id = int(data.split(":")[1])
            await handle_source_toggle(update, context, admin_service, source_id)

        elif data.startswith("tracking_detail:"):
            tracking_id = int(data.split(":")[1])
            await show_tracking_detail(update, context, admin_service, tracking_id)

        elif data.startswith("tracking_info:"):
            tracking_id = int(data.split(":")[1])
            await show_tracking_info(update, context, admin_service, tracking_id)

        elif data.startswith("stop_tracking:"):
            tracking_id = int(data.split(":")[1])
            await handle_tracking_stop(update, context, admin_service, tracking_id)

        elif data.startswith("tp_menu:"):
            tracking_id = int(data.split(":")[1])
            await show_tp_selection(update, context, admin_service, tracking_id)

        elif data.startswith("entry_menu:"):
            tracking_id = int(data.split(":")[1])
            await show_entry_selection(update, context, admin_service, tracking_id)

        elif data.startswith("send_tp:"):
            parts = data.split(":")
            tracking_id, tp_position = int(parts[1]), int(parts[2])
            await handle_send_tp(update, context, admin_service, tracking_id, tp_position)

        elif data.startswith("send_entry:"):
            parts = data.split(":")
            tracking_id, entry_position = int(parts[1]), int(parts[2])
            await handle_send_entry(update, context, admin_service, tracking_id, entry_position)

        elif data.startswith("close_tracking:"):
            tracking_id = int(data.split(":")[1])
            await show_tracking_confirmation(update, context, admin_service, tracking_id, "close")

        elif data.startswith("cancel_tracking:"):
            tracking_id = int(data.split(":")[1])
            await show_tracking_confirmation(update, context, admin_service, tracking_id, "cancel")

        elif data.startswith("confirm_close:"):
            tracking_id = int(data.split(":")[1])
            await handle_tracking_close(update, context, admin_service, tracking_id)

        elif data.startswith("confirm_cancel:"):
            tracking_id = int(data.split(":")[1])
            await handle_tracking_cancel(update, context, admin_service, tracking_id)

        elif data == "noop":
            # No operation - used for pagination display
            pass

        else:
            await query.edit_message_text("❌ Unknown action.")

    except Exception as e:
        logger.error(f"Error in callback handler: {e}")
        try:
            await query.edit_message_text(f"❌ Error: {str(e)}")
        except Exception:
            await query.message.reply_text(f"❌ Error: {str(e)}")


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, edit: bool = False) -> None:
    """Show the main admin menu."""
    try:
        stats = await admin_service.get_system_stats()
        bot_status = admin_service.get_bot_status()

        keyboard = build_main_menu(
            bot_enabled=bot_status["bot_enabled"],
            dev_mode=bot_status["dev_mode"],
            active_trackings=stats["active_trackings_count"],
            active_sources=stats["active_sources_count"],
            total_sources=stats["total_sources_count"],
        )

        bot_emoji = "🟢" if bot_status["bot_enabled"] else "🔴"
        mode_emoji = "🟡" if bot_status["dev_mode"] else "🟢"

        menu_text = (
            f"🤖 *Admin Control Panel*\n\n"
            f"{bot_emoji} Bot Status: {'Enabled' if bot_status['bot_enabled'] else 'Disabled'}\n"
            f"{mode_emoji} Mode: {'Development' if bot_status['dev_mode'] else 'Production'}\n"
            f"📋 Sources: {stats['active_sources_count']}/{stats['total_sources_count']} active\n"
            f"🎯 Active Trackings: {stats['active_trackings_count']}\n"
            #f"📊 Signals Limit: {stats['signals_limit']}"
        )

        if edit and update.callback_query:
            await safe_edit_message(
                update.callback_query,
                menu_text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(menu_text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        error_text = "❌ Error loading admin panel."
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(error_text)
        else:
            await update.message.reply_text(error_text)


async def handle_toggle_bot(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService) -> None:
    """Handle bot enable/disable toggle."""
    try:
        new_state = admin_service.toggle_bot_enabled()
        status_text = "enabled" if new_state else "disabled"

        await safe_edit_message(
            update.callback_query,
            f"🤖 Bot has been *{status_text}*.\n\nReturning to main menu...",
            parse_mode="Markdown"
        )

        # Show updated main menu after a brief message
        import asyncio
        await asyncio.sleep(1)
        await show_main_menu(update, context, admin_service, edit=True)

    except Exception as e:
        logger.error(f"Error toggling bot: {e}")
        await update.callback_query.edit_message_text(f"❌ Error toggling bot: {str(e)}")


async def handle_toggle_dev(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService) -> None:
    """Handle dev mode toggle."""
    try:
        # Show processing message
        # await safe_edit_message(update.callback_query, "⚙️ Toggling dev mode and cancelling active trackings...")

        # result = await admin_service.toggle_dev_mode()
        result = {'success': True}

        if result["success"]:
            # mode_text = "Development" if result["new_dev_mode"] else "Production"
            # closed_text = f"\n🔄 Cancelled {result['closed_trackings']} active trackings." if result["closed_trackings"] > 0 else ""
            # f"⚙️ Mode switched to *{mode_text}*{closed_text}\n\nReturning to main menu..."
            await safe_edit_message(
                update.callback_query,
                "هه هه الان میزنم سیگنالارو پاک میکنم",
                parse_mode="Markdown"
            )

            # Show updated main menu
            import asyncio
            await asyncio.sleep(2)
            await show_main_menu(update, context, admin_service, edit=True)
        else:
            await update.callback_query.edit_message_text("❌ Failed to toggle dev mode.")

    except Exception as e:
        logger.error(f"Error toggling dev mode: {e}")
        await update.callback_query.edit_message_text(f"❌ Error toggling dev mode: {str(e)}")


async def show_sources_page(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, page: int, edit: bool = False) -> None:
    """Show sources list page."""
    try:
        sources_data = await admin_service.get_sources_page(page)

        keyboard = build_sources_keyboard(
            sources=sources_data["sources"],
            current_page=sources_data["current_page"],
            total_pages=sources_data["total_pages"],
            has_prev=sources_data["has_prev"],
            has_next=sources_data["has_next"],
        )

        text = f"📋 *Signal Sources* (Page {page + 1}/{sources_data['total_pages']})\n\n"

        if not sources_data["sources"]:
            text += "No sources found."
        else:
            for source in sources_data["sources"]:
                status = "🟢 Active" if source["is_active"] else "🔴 Inactive"
                text += f"*{source['name']}*\n"
                text += f"Score: {source['score']:.1f} | {status}\n"
                text += f"Signals: {source['total_signals']} | TP Rate: {source['tp_hit_rate']:.1f}%\n\n"

        if edit:
            await safe_edit_message(update.callback_query, text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error showing sources page: {e}")
        error_text = "❌ Error loading sources."
        if edit:
            await update.callback_query.edit_message_text(error_text)
        else:
            await update.message.reply_text(error_text)


async def show_trackings_page(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, page: int, edit: bool = False) -> None:
    """Show trackings list page."""
    try:
        trackings_data = await admin_service.get_trackings_page(page)

        keyboard = build_trackings_keyboard(
            trackings=trackings_data["trackings"],
            current_page=trackings_data["current_page"],
            total_pages=trackings_data["total_pages"],
            has_prev=trackings_data["has_prev"],
            has_next=trackings_data["has_next"],
        )

        text = f"🎯 *Active Trackings* (Page {page + 1}/{trackings_data['total_pages']})\n\n"

        if not trackings_data["trackings"]:
            text += "No active trackings found."
        else:
            for tracking in trackings_data["trackings"]:
                dir_emoji = "📈" if tracking["direction"] == "LONG" else "📉"
                status_emoji = {"WAITING_ENTRY": "⏳", "TRACKING": "🎯"}.get(tracking["status"], "❓")

                text += f"{dir_emoji} *{tracking['symbol']}* {status_emoji}\n"
                text += f"Source: {tracking['source_name']}\n"
                text += f"Status: {tracking['status'].replace('_', ' ').title()}\n"
                if tracking["highest_target_hit"] > 0:
                    text += f"Highest TP: {tracking['highest_target_hit']}\n"
                text += "\n"

        if edit:
            await safe_edit_message(update.callback_query, text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error showing trackings page: {e}")
        error_text = "❌ Error loading trackings."
        if edit:
            await update.callback_query.edit_message_text(error_text)
        else:
            await update.message.reply_text(error_text)


async def handle_source_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, source_id: int) -> None:
    """Handle source toggle."""
    try:
        result = await admin_service.toggle_source_active(source_id)

        if result["success"]:
            status = "enabled" if result["new_status"] else "disabled"
            await safe_edit_message(
                update.callback_query,
                f"📋 Source *{result['source_name']}* has been {status}.\n\nReturning to sources...",
                parse_mode="Markdown"
            )

            # Return to sources page
            import asyncio
            await asyncio.sleep(1)
            await show_sources_page(update, context, admin_service, 0, edit=True)
        else:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")

    except Exception as e:
        logger.error(f"Error toggling source: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def show_tracking_info(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int) -> None:
    """Show detailed tracking information - redirects to new detail view."""
    # Redirect to the new detailed view
    await show_tracking_detail(update, context, admin_service, tracking_id)


async def show_tracking_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int, action: str) -> None:
    """Show confirmation dialog for tracking actions."""
    try:
        # Get tracking details
        trackings_data = await admin_service.get_trackings_page(0, page_size=100)
        tracking = next((t for t in trackings_data["trackings"] if t["id"] == tracking_id), None)

        if not tracking:
            await update.callback_query.edit_message_text("❌ Tracking not found.")
            return

        action_text = "close" if action == "close" else "cancel"
        confirmation_text = (
            f"⚠️ *Confirm Action*\n\n"
            f"Are you sure you want to {action_text} the tracking for:\n\n"
            f"📊 *{tracking['symbol']}* ({tracking['direction']})\n"
            f"Source: {tracking['source_name']}\n"
            f"Status: {tracking['status'].replace('_', ' ').title()}\n\n"
            f"This action cannot be undone."
        )

        keyboard = build_confirmation_keyboard(action, tracking_id)

        await safe_edit_message(
            update.callback_query,
            confirmation_text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error showing confirmation: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_tracking_close(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int) -> None:
    """Handle tracking close action."""
    try:
        result = await admin_service.close_tracking(tracking_id, "admin_close")

        if result["success"]:
            action_type = result["action_type"]
            await safe_edit_message(
                update.callback_query,
                f"✅ Tracking for *{result['symbol']}* has been {action_type}.\n\nReturning to trackings...",
                parse_mode="Markdown"
            )

            # Return to trackings page
            import asyncio
            await asyncio.sleep(1)
            await show_trackings_page(update, context, admin_service, 0, edit=True)
        else:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")

    except Exception as e:
        logger.error(f"Error closing tracking: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_tracking_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int) -> None:
    """Handle tracking cancel action."""
    try:
        result = await admin_service.cancel_tracking(tracking_id)

        if result["success"]:
            action_type = result["action_type"]
            await safe_edit_message(
                update.callback_query,
                f"✅ Tracking for *{result['symbol']}* has been {action_type}.\n\nReturning to trackings...",
                parse_mode="Markdown"
            )

            # Return to trackings page
            import asyncio
            await asyncio.sleep(1)
            await show_trackings_page(update, context, admin_service, 0, edit=True)
        else:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")

    except Exception as e:
        logger.error(f"Error cancelling tracking: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def show_tracking_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int) -> None:
    """Show detailed tracking information with action buttons."""
    try:
        result = await admin_service.get_tracking_detail(tracking_id)

        if not result["success"]:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")
            return

        tracking = result["tracking"]
        dir_emoji = "📈" if tracking["direction"] == "LONG" else "📉"

        # Build detailed info text
        info_text = (
            f"{dir_emoji} *{tracking['symbol']} Details*\n\n"
            f"Direction: {tracking['direction']}\n"
            f"Source: {tracking['source_name']}\n"
            f"Status: {tracking['status'].replace('_', ' ').title()}\n"
            f"Entry Method: {tracking['entry_method'].replace('_', ' ').title()}\n"
        )

        # Add entry information
        if tracking["entries"]:
            info_text += "\n📍 *Entry Levels:*\n"
            for entry in tracking["entries"]:
                status = "✅" if entry["touched"] else "⭕"
                info_text += f"  Entry{entry['position']}: ${entry['price']:,.2f} {status}\n"

        # Add target information
        if tracking["targets"]:
            info_text += "\n🎯 *Target Levels:*\n"
            for target in tracking["targets"]:
                status = "✅" if target["hit"] else "⭕"
                info_text += f"  TP{target['position']}: ${target['price']:,.2f} {status}\n"

        # Add current price info
        if tracking["actual_entry_price"]:
            info_text += f"\n💰 Entry Price: ${tracking['actual_entry_price']:,.2f}"

        info_text += f"\n🛡️ Stop Loss: ${tracking['current_stop_loss']:,.2f}"

        if tracking["started_at"]:
            info_text += f"\n⏰ Started: {tracking['started_at'][:19].replace('T', ' ')}"

        keyboard = build_tracking_detail_keyboard(tracking_id)

        await safe_edit_message(
            update.callback_query,
            info_text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error showing tracking detail: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_tracking_stop(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int) -> None:
    """Handle tracking stop action."""
    try:
        result = await admin_service.stop_tracking(tracking_id)

        if result["success"]:
            await safe_edit_message(
                update.callback_query,
                f"⏹️ Tracking for *{result['symbol']}* has been stopped.\n\nReturning to trackings...",
                parse_mode="Markdown"
            )

            # Return to trackings page
            import asyncio
            await asyncio.sleep(1)
            await show_trackings_page(update, context, admin_service, 0, edit=True)
        else:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")

    except Exception as e:
        logger.error(f"Error stopping tracking: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def show_tp_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int) -> None:
    """Show TP selection submenu."""
    try:
        targets = await admin_service.get_tracking_targets(tracking_id)

        if not targets:
            await update.callback_query.edit_message_text("❌ No targets found for this tracking.")
            return

        keyboard = build_tp_selection_keyboard(tracking_id, targets)

        # Build TP selection text
        text = "🎯 *Select Target Profit*\n\n"

        available_count = sum(1 for target in targets if target["available"])
        if available_count == 0:
            text += "All target profits have already been hit.\n\n"
        else:
            text += f"Available TPs: {available_count}\n\n"

        # Show all targets with status
        for target in targets:
            status = "✅ Hit" if target["hit"] else "⭕ Available" if target["available"] else "❌ N/A"
            text += f"TP{target['position']}: ${target['price']:,.2f} - {status}\n"

        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error showing TP selection: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def show_entry_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int) -> None:
    """Show entry selection submenu."""
    try:
        result = await admin_service.get_tracking_detail(tracking_id)

        if not result["success"]:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")
            return

        entries = result["tracking"]["entries"]
        keyboard = build_entry_selection_keyboard(tracking_id, entries)

        # Build entry selection text
        text = "📍 *Select Entry Level*\n\n"

        available_count = sum(1 for entry in entries if not entry["touched"])
        if available_count == 0:
            text += "All entry levels have already been touched.\n\n"
        else:
            text += f"Available Entries: {available_count}\n\n"

        # Show all entries with status
        for entry in entries:
            status = "✅ Touched" if entry["touched"] else "⭕ Available"
            text += f"Entry{entry['position']}: ${entry['price']:,.2f} - {status}\n"

        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error showing entry selection: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_send_tp(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int, tp_position: int) -> None:
    """Handle send TP hit action."""
    try:
        result = await admin_service.send_tp_hit(tracking_id, tp_position)

        if result["success"]:
            await safe_edit_message(
                update.callback_query,
                f"🎯 TP{tp_position} hit sent for *{result['symbol']}*!\n\nReturning to tracking details...",
                parse_mode="Markdown"
            )

            # Return to tracking detail page
            import asyncio
            await asyncio.sleep(1)
            await show_tracking_detail(update, context, admin_service, tracking_id)
        else:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")

    except Exception as e:
        logger.error(f"Error sending TP hit: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")


async def handle_send_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_service: AdminService, tracking_id: int, entry_position: int) -> None:
    """Handle send entry hit action."""
    try:
        result = await admin_service.send_entry_hit(tracking_id, entry_position)

        if result["success"]:
            await safe_edit_message(
                update.callback_query,
                f"📍 Entry{entry_position} hit sent for *{result['symbol']}*!\n\nReturning to tracking details...",
                parse_mode="Markdown"
            )

            # Return to tracking detail page
            import asyncio
            await asyncio.sleep(1)
            await show_tracking_detail(update, context, admin_service, tracking_id)
        else:
            await update.callback_query.edit_message_text(f"❌ {result['message']}")

    except Exception as e:
        logger.error(f"Error sending entry hit: {e}")
        await update.callback_query.edit_message_text(f"❌ Error: {str(e)}")

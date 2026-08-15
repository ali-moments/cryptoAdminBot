"""Inline keyboard builders for admin bot."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_main_menu(bot_enabled: bool, dev_mode: bool, active_trackings: int, active_sources: int) -> InlineKeyboardMarkup:
    """Build the main admin menu keyboard."""
    bot_status = "🟢 ON" if bot_enabled else "🔴 OFF"
    dev_status = "🟡 DEV" if dev_mode else "🟢 PROD"
    
    keyboard = [
        [InlineKeyboardButton(f"📊 Bot Status: {bot_status}", callback_data="toggle_bot")],
        [
            InlineKeyboardButton(f"📋 Sources ({active_sources})", callback_data="sources:0"),
            InlineKeyboardButton(f"🎯 Trackings ({active_trackings})", callback_data="trackings:0"),
        ],
        [InlineKeyboardButton(f"⚙️ Mode: {dev_status}", callback_data="toggle_dev")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="main_menu")],
    ]
    
    return InlineKeyboardMarkup(keyboard)


def build_sources_keyboard(sources: list, current_page: int, total_pages: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    """Build sources list keyboard with pagination."""
    keyboard = []
    
    # Source rows
    for source in sources:
        status_icon = "🟢" if source["is_active"] else "🔴"
        score = source["score"]
        name = source["name"][:20] + "..." if len(source["name"]) > 20 else source["name"]
        
        button_text = f"{status_icon} {name} ({score:.1f})"
        callback_data = f"src_toggle:{source['id']}"
        
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Pagination row
    nav_buttons = []
    if has_prev:
        nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"sources:{current_page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"📄 {current_page+1}/{total_pages}", callback_data="noop"))
    
    if has_next:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"sources:{current_page+1}"))
    
    keyboard.append(nav_buttons)
    
    # Back button
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(keyboard)


def build_trackings_keyboard(trackings: list, current_page: int, total_pages: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    """Build trackings list keyboard with pagination."""
    keyboard = []
    
    # Tracking rows
    for tracking in trackings:
        symbol = tracking["symbol"]
        direction = tracking["direction"]
        status = tracking["status"]
        
        # Direction emoji
        dir_emoji = "📈" if direction == "LONG" else "📉"
        
        # Status emoji
        status_emoji = {
            "WAITING_ENTRY": "⏳",
            "TRACKING": "🎯",
        }.get(status, "❓")
        
        button_text = f"{dir_emoji} {symbol} {status_emoji}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"tracking_info:{tracking['id']}")])
        
        # Action buttons for this tracking
        action_buttons = [
            InlineKeyboardButton("❌ Close", callback_data=f"close_tracking:{tracking['id']}"),
            InlineKeyboardButton("🚫 Cancel", callback_data=f"cancel_tracking:{tracking['id']}"),
        ]
        keyboard.append(action_buttons)
    
    # Pagination row
    if trackings:  # Only show pagination if there are trackings
        nav_buttons = []
        if has_prev:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"trackings:{current_page-1}"))
        
        nav_buttons.append(InlineKeyboardButton(f"📄 {current_page+1}/{total_pages}", callback_data="noop"))
        
        if has_next:
            nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"trackings:{current_page+1}"))
        
        keyboard.append(nav_buttons)
    
    # Back button
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(keyboard)


def build_confirmation_keyboard(action: str, tracking_id: int) -> InlineKeyboardMarkup:
    """Build confirmation keyboard for destructive actions."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{action}:{tracking_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="trackings:0"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
"""Inline keyboard builders for admin bot."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_main_menu(bot_enabled: bool, dev_mode: bool, active_trackings: int, active_sources: int, total_sources: int) -> InlineKeyboardMarkup:
    """Build the main admin menu keyboard."""
    bot_status = "🟢 ON" if bot_enabled else "🔴 OFF"
    dev_status = "🟡 DEV" if dev_mode else "🟢 PROD"
    
    keyboard = [
        [InlineKeyboardButton(f"📊 Bot Status: {bot_status}", callback_data="toggle_bot")],
        [
            InlineKeyboardButton(f"📋 Sources ({active_sources}/{total_sources})", callback_data="sources:0"),
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
    
    # Tracking rows - only show tracking selection buttons
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
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"tracking_detail:{tracking['id']}")])
    
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


def build_tracking_detail_keyboard(tracking_id: int) -> InlineKeyboardMarkup:
    """Build tracking detail view keyboard with action buttons."""
    keyboard = [
        # Action buttons row 1
        [
            InlineKeyboardButton("🚫 Cancel", callback_data=f"cancel_tracking:{tracking_id}"),
            InlineKeyboardButton("❌ Close", callback_data=f"close_tracking:{tracking_id}"),
        ],
        # Action buttons row 2
        [
            InlineKeyboardButton("⏹️ Stop", callback_data=f"stop_tracking:{tracking_id}"),
            InlineKeyboardButton("🎯 Send TP", callback_data=f"tp_menu:{tracking_id}"),
        ],
        # Entry button row
        [
            InlineKeyboardButton("📍 Send Entry", callback_data=f"entry_menu:{tracking_id}"),
        ],
        # Back button
        [InlineKeyboardButton("🔙 Back to Trackings", callback_data="trackings:0")],
    ]
    
    return InlineKeyboardMarkup(keyboard)


def build_tp_selection_keyboard(tracking_id: int, targets: list) -> InlineKeyboardMarkup:
    """Build TP selection submenu keyboard."""
    keyboard = []
    
    # TP target buttons
    for target in targets:
        position = target["position"]
        price = target["price"]
        hit = target["hit"]
        available = target["available"]
        
        # Only show available (unhit) targets
        if available:
            button_text = f"🎯 TP{position} (${price:,.2f})"
            callback_data = f"send_tp:{tracking_id}:{position}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Show message if no targets available
    if not any(target["available"] for target in targets):
        keyboard.append([InlineKeyboardButton("ℹ️ All TPs already hit", callback_data="noop")])
    
    # Back button
    keyboard.append([InlineKeyboardButton("🔙 Back to Details", callback_data=f"tracking_detail:{tracking_id}")])
    
    return InlineKeyboardMarkup(keyboard)


def build_entry_selection_keyboard(tracking_id: int, entries: list) -> InlineKeyboardMarkup:
    """Build entry selection submenu keyboard."""
    keyboard = []
    
    # Entry buttons
    for entry in entries:
        position = entry["position"]
        price = entry["price"]
        touched = entry["touched"]
        
        # Only show untouched entries
        if not touched:
            button_text = f"📍 Entry{position} (${price:,.2f})"
            callback_data = f"send_entry:{tracking_id}:{position}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Show message if no entries available
    if all(entry["touched"] for entry in entries):
        keyboard.append([InlineKeyboardButton("ℹ️ All entries already touched", callback_data="noop")])
    
    # Back button
    keyboard.append([InlineKeyboardButton("🔙 Back to Details", callback_data=f"tracking_detail:{tracking_id}")])
    
    return InlineKeyboardMarkup(keyboard)
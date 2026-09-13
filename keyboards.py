from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def lobby_keyboard(is_admin: bool, can_start: bool, bot_username: str | None = None):
    buttons = [[InlineKeyboardButton(text="🎮 Я ИГРАЮ", callback_data="join_game")]]
    if can_start:
        buttons.append([InlineKeyboardButton(text="▶️ НАЧАТЬ ИГРУ", callback_data="start_game")])
    buttons.append([InlineKeyboardButton(text="⚙️ НАСТРОЙКИ", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎭 НАЧАТЬ МАФИЮ", callback_data="create_game")]
    ])


def admin_game_keyboard(chat_id: int | None = None, bot_username: str | None = None):
    buttons = []
    if chat_id is not None:
        buttons.append([InlineKeyboardButton(text="🎭 МОЯ РОЛЬ", callback_data=f"my_role:{chat_id}")])
        if bot_username:
            buttons.append([InlineKeyboardButton(text="🎭 МОЙ НОЧНОЙ ХОД", url=f"https://t.me/{bot_username}?start=game_{chat_id}")])
        else:
            buttons.append([InlineKeyboardButton(text="🎭 МОЙ НОЧНОЙ ХОД", callback_data=f"my_night:{chat_id}")])
    buttons.extend([
        [InlineKeyboardButton(text="⏹ ОСТАНОВИТЬ ИГРУ", callback_data="admin_stop")],
        [InlineKeyboardButton(text="🔄 РЕСТАРТ ИГРЫ", callback_data="admin_restart")],
        [InlineKeyboardButton(text="🆕 НОВАЯ ИГРА", callback_data="admin_new_game")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def night_action_keyboard(chat_id: int, role: str):
    if role == "mafia":
        buttons = [[InlineKeyboardButton(text="🔫 ВЫБРАТЬ ЖЕРТВУ", callback_data=f"night_mafia:{chat_id}")]]
    elif role == "doctor":
        buttons = [[InlineKeyboardButton(text="💊 ВЫБРАТЬ КОГО СПАСТИ", callback_data=f"night_doctor:{chat_id}")]]
    elif role == "commissioner":
        buttons = [[
            InlineKeyboardButton(text="🔎 ПРОВЕРИТЬ", callback_data=f"night_commissioner:{chat_id}"),
            InlineKeyboardButton(text="☠️ УБИТЬ", callback_data=f"night_commissioner_kill:{chat_id}"),
        ]]
    else:
        buttons = []
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def role_keyboard(chat_id: int, role: str, is_alive: bool, phase: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎭 МОЯ РОЛЬ", callback_data=f"my_role:{chat_id}")],
    ])


def target_keyboard(chat_id: int, players: list[tuple[int, str]], prefix: str, selected_id: int | None = None, allow_cancel: bool = True):
    buttons = []
    for user_id, name in players:
        display = name if len(name) <= 28 else name[:25] + "..."
        marker = "✅ " if selected_id == user_id else ""
        buttons.append([InlineKeyboardButton(text=f"{marker}🎯 {display}", callback_data=f"{prefix}:{chat_id}:{user_id}")])
    if allow_cancel:
        buttons.append([InlineKeyboardButton(text="❌ ОТМЕНИТЬ ВЫБОР", callback_data=f"cancel_action:{chat_id}:{prefix}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vote_keyboard(chat_id: int, players: list[tuple[int, str]], selected_id: int | None = None, tie_only: bool = False):
    buttons = []
    for user_id, name in players:
        display = name if len(name) <= 28 else name[:25] + "..."
        marker = "✅ " if selected_id == user_id else ""
        buttons.append([InlineKeyboardButton(text=f"{marker}🗳 {display}", callback_data=f"vote:{chat_id}:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def postgame_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 ПРИСОЕДИНИТЬСЯ К НОВОЙ ИГРЕ", callback_data="postgame_new")],
        [InlineKeyboardButton(text="🏁 ЗАВЕРШИТЬ ИГРУ", callback_data="postgame_end")],
    ])


def private_bot_keyboard(bot_username: str | None):
    buttons = [[
        InlineKeyboardButton(text="▶️ ЗАПУСТИТЬ", callback_data="private_launch"),
        InlineKeyboardButton(text="🔄 РЕСТАРТ", callback_data="private_restart"),
    ]]
    if bot_username:
        buttons.append([InlineKeyboardButton(text="➕ ДОБАВИТЬ В ГРУППУ", url=f"https://t.me/{bot_username}?startgroup=mafia")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

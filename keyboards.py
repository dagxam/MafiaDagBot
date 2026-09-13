from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def lobby_keyboard(is_admin: bool, can_start: bool, bot_username: str | None = None):
    buttons=[[InlineKeyboardButton(text="🎮 Я ИГРАЮ",callback_data="join")],[InlineKeyboardButton(text="🤖 БОТЫ",callback_data="bots")]]
    if can_start: buttons.append([InlineKeyboardButton(text="▶️ НАЧАТЬ ИГРУ",callback_data="start")])
    buttons.append([InlineKeyboardButton(text="⚙️ НАСТРОЙКИ",callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎭 НАЧАТЬ МАФИЮ",callback_data="new")]])

def admin_game_keyboard(chat_id: int | None = None, bot_username: str | None = None):
    buttons=[]
    if chat_id is not None:
        buttons.append([InlineKeyboardButton(text="🎭 МОЯ РОЛЬ",callback_data=f"role:{chat_id}")])
        if bot_username: buttons.append([InlineKeyboardButton(text="🎭 МОЙ НОЧНОЙ ХОД",url=f"https://t.me/{bot_username}?start=game_{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def night_action_keyboard(chat_id: int, role: str):
    if role=="mafia": b=[[InlineKeyboardButton(text="🔫 ВЫБРАТЬ ЖЕРТВУ",callback_data=f"night_mafia:{chat_id}")]]
    elif role=="doctor": b=[[InlineKeyboardButton(text="💊 ВЫБРАТЬ КОГО СПАСТИ",callback_data=f"night_doctor:{chat_id}")]]
    elif role=="commissioner": b=[[InlineKeyboardButton(text="🔎 ПРОВЕРИТЬ",callback_data=f"night_commissioner:{chat_id}"),InlineKeyboardButton(text="☠️ УБИТЬ",callback_data=f"night_commissioner_kill:{chat_id}")]]
    else:b=[]
    return InlineKeyboardMarkup(inline_keyboard=b)

def target_keyboard(chat_id: int, players: list[tuple[int,str]], prefix: str, selected_id: int | None = None, allow_cancel: bool = True):
    b=[]
    for uid,name in players:
        mark="✅ " if selected_id==uid else "";b.append([InlineKeyboardButton(text=f"{mark}🎯 {name[:28]}",callback_data=f"{prefix}:{chat_id}:{uid}")])
    if allow_cancel:b.append([InlineKeyboardButton(text="❌ ОТМЕНИТЬ",callback_data=f"cancel_action:{chat_id}:{prefix}")])
    return InlineKeyboardMarkup(inline_keyboard=b)

def vote_keyboard(chat_id: int, players: list[tuple[int,str]], selected_id: int | None = None, tie_only: bool = False):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🗳 {name[:28]}",callback_data=f"vote:{chat_id}:{uid}")] for uid,name in players])

def role_keyboard(chat_id: int, role: str, is_alive: bool, phase: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎭 МОЯ РОЛЬ",callback_data=f"role:{chat_id}")]])

def postgame_keyboard(): return None

def private_bot_keyboard(bot_username: str | None = None):
    b=[]
    if bot_username:b.append([InlineKeyboardButton(text="➕ ДОБАВИТЬ В ГРУППУ",url=f"https://t.me/{bot_username}?startgroup=mafia")])
    return InlineKeyboardMarkup(inline_keyboard=b)

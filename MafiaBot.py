import asyncio
import os
import html
import random
from collections import Counter

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, FSInputFile, InputProfilePhotoStatic, BotCommand, BotCommandScopeAllGroupChats
from aiogram.enums import ChatType, ChatMemberStatus
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs): return False

from game import Game, MAFIA, DOCTOR, COMMISSIONER, ROLE_NAMES, ROLE_DESCRIPTIONS
from keyboards import lobby_keyboard, admin_start_keyboard, admin_game_keyboard, target_keyboard, vote_keyboard, night_action_keyboard, private_bot_keyboard, postgame_keyboard
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Runtime keyboard overrides keep the game UI free from host/admin control buttons.
def admin_game_keyboard(chat_id=None, bot_username=None):
    buttons=[]
    if chat_id is not None:
        buttons.append([InlineKeyboardButton(text="🎭 МОЯ РОЛЬ",callback_data=f"my_role:{chat_id}")])
        if bot_username: buttons.append([InlineKeyboardButton(text="🎭 МОЙ НОЧНОЙ ХОД",url=f"https://t.me/{bot_username}?start=game_{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def vote_keyboard(chat_id,players,selected_id=None,tie_only=False):
    buttons=[]
    for uid,name in players:
        display=name if len(name)<=28 else name[:25]+"..."; marker="🔘 " if selected_id==uid else ""
        buttons.append([InlineKeyboardButton(text=f"{marker}🗳 {display}",callback_data=f"vote_select:{chat_id}:{uid}")])
    buttons.append([InlineKeyboardButton(text="✅ ГОЛОСОВАТЬ",callback_data=f"vote_confirm:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def private_bot_keyboard(bot_username=None):
    buttons=[]
    if bot_username: buttons.append([InlineKeyboardButton(text="➕ ДОБАВИТЬ В ГРУППУ",url=f"https://t.me/{bot_username}?startgroup=mafia")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def postgame_keyboard(): return None

load_dotenv()
BOT_TOKEN=os.getenv("BOT_TOKEN")
if not BOT_TOKEN: raise RuntimeError("❌ Не найден BOT_TOKEN. Добавьте BOT_TOKEN в переменные окружения Bothost или в .env локально.")

dp=Dispatcher(); games={}; game_messages={}; game_message_ids={}; game_tasks={}; BOT_USERNAME=None
welcome_messages={}; welcome_locks={}; private_pairs={}; private_activated_users=set()

async def send_game_message(bot,game,text,**kwargs):
    m=await bot.send_message(game.chat_id,text,**kwargs); game_message_ids.setdefault(game.chat_id,set()).add(m.message_id); return m
async def delete_message_safe(bot,chat_id,message_id):
    try: await bot.delete_message(chat_id=chat_id,message_id=message_id)
    except Exception: pass
async def send_private_game_message(bot,game,user_id,text,**kwargs):
    m=await bot.send_message(user_id,text,**kwargs); private_pairs.setdefault(game.chat_id,set()).add((user_id,m.message_id)); return m
async def cleanup_game_messages(bot,chat_id):
    for message_id in set(game_message_ids.pop(chat_id,set())): await delete_message_safe(bot,chat_id,message_id)
    mid=game_messages.pop(chat_id,None)
    if mid: await delete_message_safe(bot,chat_id,mid)
    for uid,mid in set(private_pairs.pop(chat_id,set())): await delete_message_safe(bot,uid,mid)
async def is_group_admin(bot,chat_id,user_id):
    try:return (await bot.get_chat_member(chat_id,user_id)).status in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.CREATOR)
    except Exception as e: print(f"⚠️ Ошибка проверки администратора: {e}"); return False
async def bot_has_required_rights(bot,chat_id):
    try:
        me=await bot.get_me();member=await bot.get_chat_member(chat_id=chat_id,user_id=me.id)
        return member.status==ChatMemberStatus.ADMINISTRATOR and bool(getattr(member,"can_delete_messages",False))
    except Exception as e: print(f"⚠️ Ошибка проверки прав бота: {e}"); return False
def bot_admin_required_text():
    return "🚫 <b>Игра пока не может быть запущена.</b>\n\nНазначьте бота <b>администратором группы</b> и обязательно включите право <b>удалять сообщения</b>.\n\nЭто нужно для корректной работы игры и удаления сообщений погибших игроков."

def get_player_name(user):return user.full_name or "Игрок"
def safe_name(game,uid):return html.escape(game.player_names.get(uid,"Игрок"))
def login_name(game,uid):return html.escape(game.player_usernames.get(uid,safe_name(game,uid)))
def get_players_text(game):
    if not game.players:return "Пока никто не присоединился."
    return "\n".join(f"{i}. {'🔴' if game.started and uid not in game.alive else '🟢'} {safe_name(game,uid)}" for i,uid in enumerate(game.players,1))
def get_lobby_text(game):
    c=len(game.players);status=f"⏳ Нужно ещё <b>{game.MIN_PLAYERS-c} игрока</b>" if c<game.MIN_PLAYERS else "✅ <b>Минимум игроков набран!</b>"
    return f"🎭 <b>MAFIA — {html.escape(game.group_title)}</b>\n\n👥 Игроков: <b>{c}</b>\n\n{get_players_text(game)}\n\n{status}\n\n🔵 Остальные участники группы могут свободно общаться и наблюдать."
def get_welcome_text(title="MAFIA"):return f"🎭 <b>MAFIA — {html.escape(title)}</b>\n\nГотовы сыграть?\n\n👥 Минимум игроков: <b>4</b>\n🔎 Комиссар появляется с 6 игроков.\n🔒 Секретные действия выполняются приватно."
def get_game_status_text(game):return f"🎭 <b>MAFIA — {html.escape(game.group_title)}</b>\n\n👥 Игроков: <b>{len(game.players)}</b>\n🟢 Живых: <b>{len(game.alive)}</b>\n\n🌙 Ночь: <b>{game.night_number}</b>\n☀️ День: <b>{game.day_number}</b>\n\n<b>{get_phase_name(game.phase)}</b>"
def get_phase_name(p):return {"starting":"🎲 Распределение ролей","night":"🌙 Город засыпает","day_discussion":"💬 Обсуждение","day_vote":"🗳 Голосование","last_word":"🔴 Последнее слово","finished":"🏆 Игра завершена","stopped":"⏹ Игра остановлена"}.get(p,p)
def settings_keyboard():
    vals=[2,4,6,8,10]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"💬 {v} мин.",callback_data=f"setting_discussion_{v}") for v in vals[:3]],[InlineKeyboardButton(text=f"💬 {v} мин.",callback_data=f"setting_discussion_{v}") for v in vals[3:]],[InlineKeyboardButton(text="🌙 Ночь −",callback_data="setting_night_minus"),InlineKeyboardButton(text="🌙 Ночь +",callback_data="setting_night_plus")],[InlineKeyboardButton(text="🔴 Последнее слово −",callback_data="setting_lastword_minus"),InlineKeyboardButton(text="🔴 Последнее слово +",callback_data="setting_lastword_plus")],[InlineKeyboardButton(text="⬅️ НАЗАД",callback_data="settings_back")]])
def get_settings_text(game):return f"⚙️ <b>НАСТРОЙКИ</b>\n\n💬 Обсуждение: <b>{game.discussion_seconds//60} мин.</b>\n🌙 Ночь: <b>{game.night_seconds//60} мин.</b>\n🔴 Последнее слово: <b>{game.last_word_seconds} сек.</b>\n\n💬 Время обсуждения: <b>2 / 4 / 6 / 8 / 10 мин.</b>"
async def setup_bot_avatar(bot):
    path=os.path.join(os.path.dirname(os.path.abspath(__file__)),"bot_avatar.jpg")
    if not os.path.exists(path):return
    try:await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(path)))
    except Exception as e:print(f"⚠️ Не удалось установить аватар: {e}")
async def ensure_welcome_message(bot,cid,title):
    lock=welcome_locks.setdefault(cid,asyncio.Lock())
    async with lock:
        if cid in welcome_messages:return welcome_messages[cid]
        m=await bot.send_message(cid,get_welcome_text(title or "MAFIA"),reply_markup=admin_start_keyboard(),parse_mode="HTML");welcome_messages[cid]=m.message_id;game_message_ids.setdefault(cid,set()).add(m.message_id);return m.message_id

@dp.message(CommandStart())
async def start_handler(message:Message):
    bot=message.bot;me=await bot.get_me();global BOT_USERNAME;BOT_USERNAME=me.username
    if message.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP):return
    arg=(message.text or "").split(maxsplit=1)[1] if " " in (message.text or "") else "";target=None
    if arg.startswith("game_"):
        try:target=games.get(int(arg.split("_",1)[1]))
        except ValueError:pass
    uid=message.from_user.id
    if uid not in private_activated_users:
        await message.answer("🎭 <b>MAFIA</b>\n\nБот активирован.\nИспользуйте команды <b>/start</b>, <b>/reset</b> и <b>/stop</b> здесь, в личном чате.\nСекретные игровые действия выполняются автоматически через кнопку из группы.",reply_markup=private_bot_keyboard(me.username),parse_mode="HTML");private_activated_users.add(uid)
    if target and target.started and uid in target.alive and target.phase=="night":await send_current_private_action(bot,target,uid)

@dp.message(Command("reset"))
async def private_reset(message:Message,bot:Bot):
    if message.chat.type!=ChatType.PRIVATE:return
    uid=message.from_user.id;private_activated_users.discard(uid)
    await message.answer("♻️ <b>Бот сброшен.</b>\n\nЛичный интерфейс очищен. Нажмите <b>/start</b>, чтобы активировать его заново.",parse_mode="HTML")

@dp.message(Command("stop"))
async def private_stop(message:Message):
    if message.chat.type!=ChatType.PRIVATE:return
    private_activated_users.discard(message.from_user.id)
    await message.answer("⏹ <b>Личный режим остановлен.</b>\n\nДля повторной активации используйте <b>/start</b>.",parse_mode="HTML")

@dp.my_chat_member()
async def bot_added(event:ChatMemberUpdated,bot:Bot):
    chat=event.chat
    if chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP):return
    if event.new_chat_member.status not in (ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR):return
    if event.old_chat_member.status not in (ChatMemberStatus.LEFT,ChatMemberStatus.KICKED):return
    if await bot_has_required_rights(bot,chat.id):await ensure_welcome_message(bot,chat.id,chat.title or "MAFIA")
    else:await bot.send_message(chat.id,"🤖 <b>Бот добавлен в группу.</b>\n\n"+bot_admin_required_text(),parse_mode="HTML")

async def create_lobby_for_chat(bot,cid,creator,title):
    game=games.get(cid)
    if game and game.started:return None,"⚠️ Игра уже идёт."
    if game and game.players:return None,"⚠️ Уже есть открытое лобби."
    if not await bot_has_required_rights(bot,cid):return None,bot_admin_required_text()
    await cleanup_game_messages(bot,cid);welcome_messages.pop(cid,None);game=Game(chat_id=cid,creator_id=creator,group_title=title or "MAFIA");games[cid]=game
    m=await bot.send_message(cid,get_lobby_text(game),reply_markup=lobby_keyboard(True,False),parse_mode="HTML");game_messages[cid]=m.message_id;game_message_ids.setdefault(cid,set()).add(m.message_id);return game,None

@dp.message(Command("mafia"))
async def mafia_command(message:Message,bot:Bot):
    if message.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP):return
    if not await is_group_admin(bot,message.chat.id,message.from_user.id):await message.answer("⚠️ Только администратор группы.");return
    game,err=await create_lobby_for_chat(bot,message.chat.id,message.from_user.id,message.chat.title or "MAFIA")
    if err:await message.answer(err,parse_mode="HTML")

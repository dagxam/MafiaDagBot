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

# Локальные игровые клавиатуры. Они переопределяют старые функции из keyboards.py.
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
welcome_messages={}; welcome_locks={}; private_pairs={}

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
    except Exception as e:print(f"⚠️ Ошибка проверки администратора: {e}");return False

def get_player_name(user):return user.full_name or "Игрок"
def safe_name(game,uid):return html.escape(game.player_names.get(uid,"Игрок"))
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
    if message.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP):
        game=games.get(message.chat.id)
        if game and (game.started or game.players):return
        await ensure_welcome_message(bot,message.chat.id,message.chat.title or "MAFIA");return
    arg=(message.text or "").split(maxsplit=1)[1] if " " in (message.text or "") else "";target=None
    if arg.startswith("game_"):
        try:target=games.get(int(arg.split("_",1)[1]))
        except ValueError:pass
    await message.answer("🎭 <b>MAFIA</b>\n\nЛичный игровой интерфейс открыт.\nСекретные действия и результаты видны только вам.",reply_markup=private_bot_keyboard(me.username),parse_mode="HTML")
    if target and target.started and message.from_user.id in target.alive and target.phase=="night":await send_current_private_action(bot,target,message.from_user.id)

@dp.my_chat_member()
async def bot_added(event:ChatMemberUpdated,bot:Bot):
    chat=event.chat
    if chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP):return
    if event.new_chat_member.status not in (ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR):return
    if event.old_chat_member.status not in (ChatMemberStatus.LEFT,ChatMemberStatus.KICKED):return
    await ensure_welcome_message(bot,chat.id,chat.title or "MAFIA")

async def create_lobby_for_chat(bot,cid,creator,title):
    game=games.get(cid)
    if game and game.started:return None,"⚠️ Игра уже идёт."
    if game and game.players:return None,"⚠️ Уже есть открытое лобби."
    await cleanup_game_messages(bot,cid);welcome_messages.pop(cid,None);game=Game(chat_id=cid,creator_id=creator,group_title=title or "MAFIA");games[cid]=game
    m=await bot.send_message(cid,get_lobby_text(game),reply_markup=lobby_keyboard(True,False),parse_mode="HTML");game_messages[cid]=m.message_id;game_message_ids.setdefault(cid,set()).add(m.message_id);return game,None

@dp.message(Command("mafia"))
async def mafia_command(message:Message,bot:Bot):
    if message.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP):return
    if not await is_group_admin(bot,message.chat.id,message.from_user.id):await message.answer("⚠️ Только администратор группы.");return
    _,err=await create_lobby_for_chat(bot,message.chat.id,message.from_user.id,message.chat.title or "MAFIA")
    if err:await message.answer(err)
@dp.message(Command("stop"))
async def stop_command(message:Message,bot:Bot):
    if message.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP):return
    if not await is_group_admin(bot,message.chat.id,message.from_user.id):await message.answer("⚠️ Только администратор группы.");return
    cid=message.chat.id;game=games.get(cid)
    if game:game.stop()
    task=game_tasks.get(cid)
    if task and not task.done():task.cancel()
    await cleanup_game_messages(bot,cid);welcome_messages.pop(cid,None);await ensure_welcome_message(bot,cid,message.chat.title or "MAFIA")
@dp.message(Command("restart"))
async def restart_command(message:Message,bot:Bot):
    if message.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP):return
    if not await is_group_admin(bot,message.chat.id,message.from_user.id):await message.answer("⚠️ Только администратор группы.");return
    cid=message.chat.id;game=games.get(cid)
    if not game or not game.can_start():await message.answer("❌ Для рестарта нужно минимум 4 игрока.");return
    task=game_tasks.get(cid)
    if task and not task.done():task.cancel()
    await cleanup_game_messages(bot,cid);game.restart();m=await bot.send_message(cid,f"🔄 <b>ИГРА ПЕРЕЗАПУСКАЕТСЯ</b>\n\n👥 Игроков: <b>{len(game.players)}</b>\n\n🎲 Роли будут распределены заново.",reply_markup=admin_game_keyboard(cid,BOT_USERNAME),parse_mode="HTML");game_messages[cid]=m.message_id;game_message_ids.setdefault(cid,set()).add(m.message_id);await start_game_task(bot,game)

@dp.callback_query(F.data=="create_game")
async def create_game(callback:CallbackQuery,bot:Bot):
    if not callback.message:return
    if not await is_group_admin(bot,callback.message.chat.id,callback.from_user.id):await callback.answer("⚠️ Только администратор группы.",show_alert=True);return
    game,err=await create_lobby_for_chat(bot,callback.message.chat.id,callback.from_user.id,callback.message.chat.title or "MAFIA");await callback.answer(err or "🎭 Игровая комната создана!",show_alert=bool(err))
@dp.callback_query(F.data=="join_game")
async def join_game(callback:CallbackQuery,bot:Bot):
    if not callback.message:return
    game=games.get(callback.message.chat.id);uid=callback.from_user.id
    if not game or game.started:await callback.answer("❌ Сейчас нельзя присоединиться.",show_alert=True);return
    if uid in game.players:await callback.answer("🎭 Вы уже участвуете!",show_alert=True);return
    game.add_player(uid);game.player_names[uid]=get_player_name(callback.from_user)
    if callback.from_user.username:game.player_usernames[uid]="@"+callback.from_user.username
    await callback.message.edit_text(get_lobby_text(game),reply_markup=lobby_keyboard(True,game.can_start()),parse_mode="HTML");await callback.answer("🎮 Вы присоединились!")
@dp.callback_query(F.data=="settings")
async def settings(callback:CallbackQuery,bot:Bot):
    if not callback.message:return
    if not await is_group_admin(bot,callback.message.chat.id,callback.from_user.id):await callback.answer("⚠️ Только администратор.",show_alert=True);return
    game=games.get(callback.message.chat.id)
    if game:await callback.message.edit_text(get_settings_text(game),reply_markup=settings_keyboard(),parse_mode="HTML");await callback.answer()
@dp.callback_query(F.data.startswith("setting_"))
async def setting_change(callback:CallbackQuery,bot:Bot):
    if not callback.message:return
    if not await is_group_admin(bot,callback.message.chat.id,callback.from_user.id):await callback.answer("⚠️ Только администратор.",show_alert=True);return
    game=games.get(callback.message.chat.id)
    if not game:return
    d=callback.data
    if d.startswith("setting_discussion_"):
        try:game.discussion_seconds=int(d.rsplit("_",1)[1])*60
        except ValueError:pass
    elif d=="setting_night_minus":game.night_seconds=max(60,game.night_seconds-60)
    elif d=="setting_night_plus":game.night_seconds=min(300,game.night_seconds+60)
    elif d=="setting_lastword_minus":game.last_word_seconds=max(10,game.last_word_seconds-10)
    elif d=="setting_lastword_plus":game.last_word_seconds=min(60,game.last_word_seconds+10)
    await callback.message.edit_text(get_settings_text(game),reply_markup=settings_keyboard(),parse_mode="HTML");await callback.answer("⚙️ Настройка изменена.")
@dp.callback_query(F.data=="settings_back")
async def settings_back(callback:CallbackQuery):
    if not callback.message:return
    game=games.get(callback.message.chat.id)
    if game:await callback.message.edit_text(get_lobby_text(game),reply_markup=lobby_keyboard(True,game.can_start()),parse_mode="HTML");await callback.answer()
@dp.callback_query(F.data=="start_game")
async def start_game(callback:CallbackQuery,bot:Bot):
    if not callback.message:return
    cid=callback.message.chat.id;game=games.get(cid)
    if not game:return
    if not await is_group_admin(bot,cid,callback.from_user.id):await callback.answer("⚠️ Только администратор.",show_alert=True);return
    if not game.can_start():await callback.answer("❌ Нужно минимум 4 игрока.",show_alert=True);return
    if game.started:await callback.answer("🌙 Игра уже идёт.",show_alert=True);return
    game.start();await callback.message.edit_text(f"🎭 <b>ИГРА НАЧИНАЕТСЯ</b>\n\n👥 Игроков: <b>{len(game.players)}</b>\n\n🔒 Роли и ночные действия не публикуются.",reply_markup=admin_game_keyboard(cid,BOT_USERNAME),parse_mode="HTML");game_messages[cid]=callback.message.message_id;game_message_ids.setdefault(cid,set()).add(callback.message.message_id);await callback.answer("🎭 Игра началась!");await start_game_task(bot,game)
async def start_game_task(bot,game):
    old=game_tasks.get(game.chat_id)
    if old and not old.done():old.cancel()
    game_tasks[game.chat_id]=asyncio.create_task(run_game(bot,game))
async def run_game(bot,game):
    try:
        game.assign_roles();await send_game_message(bot,game,"🎲 <b>РОЛИ РАСПРЕДЕЛЕНЫ</b>\n\n🔒 Каждый игрок получил свою секретную роль.\n\n🌙 <b>ГОРОД ЗАСЫПАЕТ</b>",parse_mode="HTML");await asyncio.sleep(1)
        while game.started:
            w=game.winner()
            if w:await finish_game(bot,game,w);return
            await run_night(bot,game)
            if not game.started:return
            w=game.winner()
            if w:await finish_game(bot,game,w);return
            await run_day(bot,game)
            if not game.started:return
            w=game.winner()
            if w:await finish_game(bot,game,w);return
    except asyncio.CancelledError:raise
    except Exception as e:
        print(f"❌ Ошибка игры {game.chat_id}: {e}")
        if game.started:await send_game_message(bot,game,"⚠️ Произошла техническая ошибка игрового процесса.",parse_mode="HTML")
async def send_current_private_action(bot,game,uid):
    role=game.roles.get(uid)
    if role==MAFIA:
        t=[(x,game.player_names.get(x,"Игрок")) for x in game.alive if game.roles.get(x)!=MAFIA];await send_private_game_message(bot,game,uid,"🔫 <b>ХОД МАФИИ</b>\n\nКого устранить?",reply_markup=target_keyboard(game.chat_id,t,"mafia_target",game.mafia_votes.get(uid)),parse_mode="HTML")
    elif role==DOCTOR:
        t=[(x,game.player_names.get(x,"Игрок")) for x in game.doctor_targets()]
        if t:await send_private_game_message(bot,game,uid,"💊 <b>ХОД ДОКТОРА</b>\n\nКого спасти?",reply_markup=target_keyboard(game.chat_id,t,"doctor_target",game.doctor_target),parse_mode="HTML")
    elif role==COMMISSIONER:await send_private_game_message(bot,game,uid,"🔎 <b>ХОД КОМИССАРА</b>\n\nВыберите действие.",reply_markup=night_action_keyboard(game.chat_id,role),parse_mode="HTML")
async def run_mafia_phase(bot,game):
    mafia=game.alive_mafia()
    if not mafia:return
    await send_game_message(bot,game,"🔫 <b>ХОД МАФИИ</b>\n\nМафия выбирает жертву...\n🔒 Выбор выполняется приватно.",parse_mode="HTML")
    for uid in mafia:
        t=[(x,game.player_names.get(x,"Игрок")) for x in game.alive if game.roles.get(x)!=MAFIA]
        try:await send_private_game_message(bot,game,uid,"🔫 <b>ХОД МАФИИ</b>\n\nКого устранить?",reply_markup=target_keyboard(game.chat_id,t,"mafia_target",game.mafia_votes.get(uid)),parse_mode="HTML")
        except Exception:pass
    game.action_event.clear()
    try:await asyncio.wait_for(game.action_event.wait(),timeout=game.night_seconds)
    except asyncio.TimeoutError:pass
    for uid in mafia:
        if uid not in game.mafia_votes:
            t=[x for x in game.alive if game.roles.get(x)!=MAFIA]
            if t:game.mafia_votes[uid]=random.choice(t)
async def run_doctor_phase(bot,game):
    doctor=next((x for x in game.alive if game.roles.get(x)==DOCTOR),None)
    if doctor is None:return
    await send_game_message(bot,game,"💊 <b>ХОД ДОКТОРА</b>\n\nДоктор выбирает, кого спасти...\n🔒 Выбор выполняется приватно.",parse_mode="HTML")
    t=game.doctor_targets()
    if not t:return
    try:await send_private_game_message(bot,game,doctor,"💊 <b>ХОД ДОКТОРА</b>\n\nКого спасти?",reply_markup=target_keyboard(game.chat_id,[(x,game.player_names.get(x,"Игрок")) for x in t],"doctor_target",game.doctor_target),parse_mode="HTML")
    except Exception:pass
    game.action_event.clear()
    try:await asyncio.wait_for(game.action_event.wait(),timeout=game.night_seconds)
    except asyncio.TimeoutError:pass
    if game.doctor_target is None:game.doctor_target=random.choice(t)
async def run_commissioner_phase(bot,game):
    c=next((x for x in game.alive if game.roles.get(x)==COMMISSIONER),None)
    if c is None:return
    await send_game_message(bot,game,"🔎 <b>ХОД КОМИССАРА</b>\n\nКомиссар проверяет игрока или выходит на охоту...\n🔒 Действие выполняется приватно.",parse_mode="HTML")
    try:await send_private_game_message(bot,game,c,"🔎 <b>ХОД КОМИССАРА</b>\n\nВыберите действие.",reply_markup=night_action_keyboard(game.chat_id,COMMISSIONER),parse_mode="HTML")
    except Exception:pass
    game.action_event.clear()
    try:await asyncio.wait_for(game.action_event.wait(),timeout=game.night_seconds)
    except asyncio.TimeoutError:pass
    if game.commissioner_target is None:
        t=[x for x in game.alive if x!=c]
        if t:game.commissioner_target=random.choice(t)
async def run_night(bot,game):
    game.night_number+=1;game.phase="night";game.reset_night_actions();await update_main_game_message(bot,game);await send_game_message(bot,game,f"🌙 <b>ГОРОД ЗАСЫПАЕТ — НОЧЬ {game.night_number}</b>\n\n🔒 Все ночные действия выполняются тайно.",parse_mode="HTML");await run_mafia_phase(bot,game)
    if not game.started:return
    await run_doctor_phase(bot,game)
    if not game.started:return
    await run_commissioner_phase(bot,game)
    if not game.started:return
    deaths=resolve_night(game)
    if not deaths:await send_game_message(bot,game,"☀️ <b>ГОРОД ПРОСЫПАЕТСЯ</b>\n\nЭтой ночью никто не погиб.",parse_mode="HTML")
    else:
        await send_game_message(bot,game,"☀️ <b>ГОРОД ПРОСЫПАЕТСЯ</b>\n\nНочью погибли: "+", ".join(safe_name(game,x) for x in deaths),parse_mode="HTML")
        for x in deaths:await run_last_word(bot,game,x)
def resolve_night(game):
    deaths=set()
    if game.mafia_votes:
        c=Counter(game.mafia_votes.values());h=max(c.values());deaths.add(random.choice([u for u,v in c.items() if v==h]))
    if game.commissioner_kill_target is not None:deaths.add(game.commissioner_kill_target)
    if game.doctor_target in deaths:deaths.remove(game.doctor_target)
    deaths={u for u in deaths if u in game.alive}
    for u in deaths:game.alive.discard(u)
    if game.doctor_target is not None:game.doctor_healed.add(game.doctor_target)
    return list(deaths)
async def run_last_word(bot,game,player_id):
    game.phase="last_word";game.last_word_player=player_id;game.last_word_text=None;game.action_event.clear();await update_main_game_message(bot,game)
    login=game.player_usernames.get(player_id,safe_name(game,player_id));await send_game_message(bot,game,f"🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n☠️ <b>{login}</b> убит.\n\n💬 Оставьте одно последнее сообщение.\n⏱ {game.last_word_seconds} сек.",parse_mode="HTML")
    try:await asyncio.wait_for(game.action_event.wait(),timeout=game.last_word_seconds)
    except asyncio.TimeoutError:pass
    game.last_word_used.add(player_id);game.last_word_player=None;await update_main_game_message(bot,game)
async def run_day(bot,game):
    game.day_number+=1;game.phase="day_discussion";await update_main_game_message(bot,game);await send_game_message(bot,game,f"☀️ <b>ДЕНЬ</b>\n\n💬 <b>ОБСУЖДЕНИЕ</b>\n\nОбсуждение длится <b>{game.discussion_seconds//60} мин.</b>",parse_mode="HTML");await asyncio.sleep(game.discussion_seconds)
    if game.started:await conduct_vote(bot,game,None)
async def vote_status_text(game,candidates):
    lines=["🗳 <b>ГОЛОСОВАНИЕ</b>","","👥 Кто за кого проголосовал:"]+[f"👤 {safe_name(game,v)} → 🎯 {safe_name(game,t)}" for v,t in game.day_votes.items()] or ["Пока никто не подтвердил голос."]
    lines += ["",f"⏳ Не проголосовали: <b>{len([u for u in game.alive_players() if u not in game.day_votes])}</b>"]
    return "\n".join(lines)
async def conduct_vote(bot,game,candidates):
    game.phase="day_vote"
    if candidates is None:game.tie_candidates.clear()
    game.reset_day_votes();ids=[u for u in (candidates if candidates is not None else game.alive_players()) if u in game.alive];players=[(u,game.player_names.get(u,"Игрок")) for u in ids]
    await update_main_game_message(bot,game);m=await send_game_message(bot,game,vote_status_text(game,ids),reply_markup=vote_keyboard(game.chat_id,players),parse_mode="HTML");game.vote_message_id=m.message_id;game.action_event.clear()
    try:await asyncio.wait_for(game.action_event.wait(),timeout=game.vote_seconds)
    except asyncio.TimeoutError:pass
    for voter in game.alive_players():
        if voter not in game.day_votes:
            selected=game.day_vote_selection.get(voter);choices=[u for u in ids if u in game.alive and u!=voter]
            if selected in choices:game.day_votes[voter]=selected
            elif choices:game.day_votes[voter]=random.choice(choices)
    await publish_vote_results(bot,game);counts=Counter(game.day_votes.values())
    if not counts:return
    high=max(counts.values());leaders=[u for u,c in counts.items() if c==high]
    if len(leaders)>1 and candidates is None:
        game.tie_candidates=leaders;await send_game_message(bot,game,"⚖️ <b>НИЧЬЯ</b>\n\nРешающее голосование между лидерами.",parse_mode="HTML");await conduct_vote(bot,game,leaders);return
    if len(leaders)>1:await send_game_message(bot,game,"⚖️ <b>СНОВА НИЧЬЯ</b>\n\nНикто не изгнан.",parse_mode="HTML");return
    eliminated=leaders[0]
    if eliminated in game.alive:game.alive.remove(eliminated)
    await send_game_message(bot,game,f"🔴 <b>{safe_name(game,eliminated)}</b> покидает игру.",parse_mode="HTML");await run_last_word(bot,game,eliminated)
async def publish_vote_results(bot,game):
    lines=["🗳 <b>РЕЗУЛЬТАТЫ ГОЛОСОВАНИЯ</b>","","👥 Кто за кого:"]+[f"👤 {safe_name(game,v)} → 🎯 {safe_name(game,t)}" for v,t in game.day_votes.items()]+["","📊 <b>ИТОГ:</b>"]+[f"{safe_name(game,u)} — <b>{c}</b> голос(а)" for u,c in Counter(game.day_votes.values()).most_common()];await send_game_message(bot,game,"\n".join(lines),parse_mode="HTML")
@dp.callback_query(F.data.startswith("vote_select:"))
async def vote_select(callback:CallbackQuery):
    try:_,cs,ts=callback.data.split(":");cid=int(cs);target=int(ts)
    except Exception:await callback.answer("❌ Некорректный выбор.",show_alert=True);return
    game=games.get(cid);v=callback.from_user.id
    if not game or game.phase!="day_vote" or v not in game.alive or target not in game.alive or target==v:await callback.answer("❌ Голосование недоступно.",show_alert=True);return
    candidates=game.tie_candidates or game.alive_players()
    if target not in candidates:await callback.answer("❌ Этот игрок не участвует в голосовании.",show_alert=True);return
    game.day_vote_selection[v]=target;players=[(u,game.player_names.get(u,"Игрок")) for u in candidates if u in game.alive]
    try:await callback.message.edit_reply_markup(reply_markup=vote_keyboard(cid,players,target))
    except Exception:pass
    await callback.answer("Выбрано. Нажмите «✅ ГОЛОСОВАТЬ».")
@dp.callback_query(F.data.startswith("vote_confirm:"))
async def vote_confirm(callback:CallbackQuery,bot:Bot):
    try:_,cs=callback.data.split(":");cid=int(cs)
    except Exception:await callback.answer("❌ Некорректное голосование.",show_alert=True);return
    game=games.get(cid);v=callback.from_user.id
    if not game or game.phase!="day_vote" or v not in game.alive:await callback.answer("❌ Голосование недоступно.",show_alert=True);return
    target=game.day_vote_selection.get(v);candidates=game.tie_candidates or game.alive_players()
    if target not in candidates or target==v:await callback.answer("⚠️ Сначала выберите игрока.",show_alert=True);return
    game.day_votes[v]=target;await callback.answer(f"🗳 Голос принят: {game.player_names.get(target,'Игрок')}")
    if callback.message:
        players=[(u,game.player_names.get(u,"Игрок")) for u in candidates if u in game.alive]
        try:await callback.message.edit_text(vote_status_text(game,candidates),reply_markup=vote_keyboard(cid,players),parse_mode="HTML");game.vote_message_id=callback.message.message_id
        except Exception:pass
    if game.all_day_votes_complete():game.action_event.set()
@dp.callback_query(F.data.startswith("mafia_target:"))
async def mafia_target(callback:CallbackQuery,bot:Bot):await handle_private_target(callback,MAFIA)
@dp.callback_query(F.data.startswith("doctor_target:"))
async def doctor_target(callback:CallbackQuery,bot:Bot):await handle_private_target(callback,DOCTOR)
async def handle_private_target(callback,role):
    try:_,cs,ts=callback.data.split(":");cid=int(cs);target=int(ts)
    except Exception:await callback.answer("❌ Некорректная кнопка.",show_alert=True);return
    game=games.get(cid);uid=callback.from_user.id
    if not game or game.phase!="night" or uid not in game.alive or game.roles.get(uid)!=role:await callback.answer("❌ Действие недоступно.",show_alert=True);return
    if target not in game.alive:await callback.answer("❌ Игрок уже мёртв.",show_alert=True);return
    if role==MAFIA and game.roles.get(target)==MAFIA:await callback.answer("❌ Нельзя выбрать союзника.",show_alert=True);return
    if role==DOCTOR and target in game.doctor_healed:await callback.answer("❌ Этого игрока уже лечили в этой игре.",show_alert=True);return
    if role==MAFIA:game.mafia_votes[uid]=target
    else:game.doctor_target=target
    if callback.message:await callback.message.delete()
    await callback.message.answer(("🔫 <b>ЦЕЛЬ МАФИИ:</b>\n\n" if role==MAFIA else "💊 <b>ЦЕЛЬ ДОКТОРА:</b>\n\n")+safe_name(game,target),parse_mode="HTML")
    if role==MAFIA and len(game.mafia_votes)>=len(game.alive_mafia()):game.action_event.set()
    if role==DOCTOR:game.action_event.set()
@dp.callback_query(F.data.startswith("night_commissioner:"))
async def commissioner_open(callback:CallbackQuery,bot:Bot):
    try:cid=int(callback.data.split(":")[1])
    except Exception:await callback.answer("❌ Некорректная игра.",show_alert=True);return
    game=games.get(cid);uid=callback.from_user.id
    if not game or game.phase!="night" or uid not in game.alive or game.roles.get(uid)!=COMMISSIONER:await callback.answer("❌ Действие недоступно.",show_alert=True);return
    t=[(u,game.player_names.get(u,"Игрок")) for u in game.alive if u!=uid]
    if callback.message:await callback.message.delete()
    await send_private_game_message(bot,game,uid,"🔎 <b>КОГО ПРОВЕРИТЬ?</b>",reply_markup=target_keyboard(cid,t,"commissioner_target",game.commissioner_target),parse_mode="HTML");await callback.answer()
@dp.callback_query(F.data.startswith("night_commissioner_kill:"))
async def commissioner_kill_open(callback:CallbackQuery,bot:Bot):
    try:cid=int(callback.data.split(":")[1])
    except Exception:await callback.answer("❌ Некорректная игра.",show_alert=True);return
    game=games.get(cid);uid=callback.from_user.id
    if not game or game.phase!="night" or uid not in game.alive or game.roles.get(uid)!=COMMISSIONER:await callback.answer("❌ Действие недоступно.",show_alert=True);return
    t=[(u,game.player_names.get(u,"Игрок")) for u in game.alive if u!=uid]
    if callback.message:await callback.message.delete()
    await send_private_game_message(bot,game,uid,"☠️ <b>КОГО УБИТЬ?</b>",reply_markup=target_keyboard(cid,t,"commissioner_kill_target",game.commissioner_kill_target),parse_mode="HTML");await callback.answer()
@dp.callback_query(F.data.startswith("commissioner_target:"))
async def commissioner_target(callback:CallbackQuery,bot:Bot):
    try:_,cs,ts=callback.data.split(":");cid=int(cs);target=int(ts)
    except Exception:await callback.answer("❌ Некорректная кнопка.",show_alert=True);return
    game=games.get(cid);uid=callback.from_user.id
    if not game or game.phase!="night" or uid not in game.alive or game.roles.get(uid)!=COMMISSIONER or target not in game.alive or target==uid:await callback.answer("❌ Действие недоступно.",show_alert=True);return
    game.commissioner_target=target;result={MAFIA:"🔴 МАФИЯ",DOCTOR:"💊 ДОКТОР",COMMISSIONER:"🔎 КОМИССАР"}.get(game.roles.get(target),"🟢 МИРНЫЙ ЖИТЕЛЬ")
    if callback.message:await callback.message.delete()
    await send_private_game_message(bot,game,uid,f"🔎 <b>РЕЗУЛЬТАТ ПРОВЕРКИ</b>\n\n{safe_name(game,target)}\n\n<b>{result}</b>",reply_markup=night_action_keyboard(cid,COMMISSIONER),parse_mode="HTML");await callback.answer("🔎 Проверка завершена.",show_alert=True)
@dp.callback_query(F.data.startswith("commissioner_kill_target:"))
async def commissioner_kill(callback:CallbackQuery,bot:Bot):
    try:_,cs,ts=callback.data.split(":");cid=int(cs);target=int(ts)
    except Exception:await callback.answer("❌ Некорректная кнопка.",show_alert=True);return
    game=games.get(cid);uid=callback.from_user.id
    if not game or game.phase!="night" or uid not in game.alive or game.roles.get(uid)!=COMMISSIONER or target not in game.alive or target==uid:await callback.answer("❌ Действие недоступно.",show_alert=True);return
    game.commissioner_kill_target=target
    if callback.message:await callback.message.delete()
    await send_private_game_message(bot,game,uid,f"☠️ <b>КОМИССАР ВЫШЕЛ НА ОХОТУ</b>\n\nЦель: {safe_name(game,target)}",parse_mode="HTML");await callback.answer("☠️ Убийство принято.")
@dp.callback_query(F.data.startswith("my_role:"))
async def my_role(callback:CallbackQuery):
    try:cid=int(callback.data.split(":")[1])
    except Exception:await callback.answer("❌ Некорректная игра.",show_alert=True);return
    game=games.get(cid)
    if not game or callback.from_user.id not in game.players:await callback.answer("🔵 Вы наблюдатель.",show_alert=True);return
    role=game.roles.get(callback.from_user.id)
    if not role:await callback.answer("❌ Роль ещё не распределена.",show_alert=True);return
    await callback.answer(f"{ROLE_NAMES[role]}\n\n{ROLE_DESCRIPTIONS[role]}",show_alert=True)
async def update_main_game_message(bot,game):
    mid=game_messages.get(game.chat_id)
    if not mid:return
    try:await bot.edit_message_text(chat_id=game.chat_id,message_id=mid,text=get_game_status_text(game) if game.started else get_welcome_text(game.group_title),reply_markup=admin_game_keyboard(game.chat_id,BOT_USERNAME) if game.started else admin_start_keyboard(),parse_mode="HTML")
    except Exception:pass
@dp.callback_query(F.data=="admin_stop")
async def admin_stop_unused(callback:CallbackQuery):await callback.answer("Используйте /stop.",show_alert=True)
@dp.callback_query(F.data=="admin_restart")
async def admin_restart_unused(callback:CallbackQuery):await callback.answer("Используйте /restart.",show_alert=True)
@dp.callback_query(F.data=="admin_new_game")
async def admin_new_unused(callback:CallbackQuery):await callback.answer("Используйте /mafia.",show_alert=True)
@dp.callback_query(F.data.startswith("my_night:"))
async def my_night(callback:CallbackQuery):await callback.answer("🔐 Откройте личный чат с ботом через «🎭 МОЙ НОЧНОЙ ХОД».",show_alert=True)
@dp.message()
async def group_messages(message:Message,bot:Bot):
    if message.chat.type==ChatType.PRIVATE:return
    game=games.get(message.chat.id)
    if not game:return
    uid=message.from_user.id if message.from_user else None
    if uid is None:return
    if game.started and game.phase=="last_word" and uid==game.last_word_player and message.text:
        text=html.escape(message.text.strip())
        if not text:return
        game.last_word_text=text;await delete_message_safe(bot,message.chat.id,message.message_id);login=game.player_usernames.get(uid,safe_name(game,uid))
        await send_game_message(bot,game,f"🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n☠️ <b>{login} убит.</b>\n\n💬 <b>Последнее сообщение:</b>\n<blockquote>«{text}»</blockquote>",parse_mode="HTML");game.action_event.set();return
    if game.started and uid in game.players and uid not in game.alive:await delete_message_safe(bot,message.chat.id,message.message_id)
async def finish_game(bot,game,winner):
    game.started=False;game.phase="finished";game.action_event.set();await update_main_game_message(bot,game);await send_game_message(bot,game,f"🏆 <b>ИГРА ОКОНЧЕНА</b>\n\n{'🔫 <b>МАФИЯ ПОБЕДИЛА!</b>' if winner=='mafia' else '🏆 <b>ГОРОД ПОБЕДИЛ!</b>'}\n\nДля новой игры администратор использует <b>/mafia</b>.",parse_mode="HTML")
async def main():
    global BOT_USERNAME
    bot=Bot(token=BOT_TOKEN);me=await bot.get_me();BOT_USERNAME=me.username;print("🟢 Mafia Bot запускается...");await setup_bot_avatar(bot)
    await bot.set_my_commands([BotCommand(command="mafia",description="Начать новую Мафию"),BotCommand(command="stop",description="Остановить игру"),BotCommand(command="restart",description="Перезапустить игру")],scope=BotCommandScopeAllGroupChats());print("🟢 Команды /mafia /stop /restart зарегистрированы");print("🟢 Mafia Bot запущен")
    try:await dp.start_polling(bot)
    finally:await bot.session.close()
if __name__=="__main__":asyncio.run(main())

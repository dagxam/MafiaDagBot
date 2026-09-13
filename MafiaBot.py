import asyncio
import os
import html
import random
from collections import Counter

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, FSInputFile, InputProfilePhotoStatic
from aiogram.enums import ChatType, ChatMemberStatus
from dotenv import load_dotenv

from game import Game, MAFIA, DOCTOR, COMMISSIONER, ROLE_NAMES, ROLE_DESCRIPTIONS
from keyboards import (
    lobby_keyboard, admin_start_keyboard, admin_game_keyboard,
    target_keyboard, vote_keyboard, night_action_keyboard,
    private_bot_keyboard, postgame_keyboard,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ Не найден BOT_TOKEN в файле .env")

dp = Dispatcher()
games: dict[int, Game] = {}
game_messages: dict[int, int] = {}
game_message_ids: dict[int, set[int]] = {}
game_tasks: dict[int, asyncio.Task] = {}
BOT_USERNAME: str | None = None


async def send_game_message(bot: Bot, game: Game, text: str, **kwargs):
    message = await bot.send_message(game.chat_id, text, **kwargs)
    game_message_ids.setdefault(game.chat_id, set()).add(message.message_id)
    return message


async def delete_message_safe(bot: Bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# Секретные сообщения хранятся парами (user_id, message_id),
# чтобы их можно было удалить из личных чатов при завершении игры.
private_pairs: dict[int, set[tuple[int, int]]] = {}


async def send_private_game_message(bot: Bot, game: Game, user_id: int, text: str, **kwargs):
    # Секретные игровые сообщения всегда отправляются только в личный чат игрока.
    message = await bot.send_message(user_id, text, **kwargs)
    private_pairs.setdefault(game.chat_id, set()).add((user_id, message.message_id))
    return message


async def cleanup_game_messages(bot: Bot, chat_id: int):
    for message_id in set(game_message_ids.pop(chat_id, set())):
        await delete_message_safe(bot, chat_id, message_id)
    main_id = game_messages.pop(chat_id, None)
    if main_id:
        await delete_message_safe(bot, chat_id, main_id)
    for user_id, message_id in set(private_pairs.pop(chat_id, set())):
        await delete_message_safe(bot, user_id, message_id)


async def is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception as error:
        print(f"⚠️ Ошибка проверки администратора: {error}")
        return False


def get_player_name(user) -> str:
    return user.full_name or "Игрок"


def safe_name(game: Game, user_id: int) -> str:
    return html.escape(game.player_names.get(user_id, "Игрок"))


def get_players_text(game: Game) -> str:
    if not game.players:
        return "Пока никто не присоединился."
    result = []
    for index, uid in enumerate(game.players, 1):
        icon = "🔴" if game.started and uid not in game.alive else "🟢"
        result.append(f"{index}. {icon} {safe_name(game, uid)}")
    return "\n".join(result)


def get_lobby_text(game: Game) -> str:
    count = len(game.players)
    status = "⏳ Нужно ещё <b>{} игрока</b>".format(game.MIN_PLAYERS - count) if count < game.MIN_PLAYERS else "✅ <b>Минимум игроков набран!</b>"
    return (
        f"🎭 <b>MAFIA — {html.escape(game.group_title)}</b>\n\n"
        f"👥 Игроков: <b>{count}</b>\n\n"
        f"{get_players_text(game)}\n\n"
        f"{status}\n\n"
        "🔵 Остальные участники группы могут свободно общаться и наблюдать."
    )


def get_welcome_text(group_title: str = "MAFIA") -> str:
    return (
        f"🎭 <b>MAFIA — {html.escape(group_title)}</b>\n\n"
        "Готовы сыграть?\n\n"
        "👥 Минимум игроков: <b>4</b>\n"
        "🔎 Комиссар появляется с 6 игроков.\n"
        "🔒 Секретные действия выполняются приватно."
    )


def get_game_status_text(game: Game) -> str:
    return (
        f"🎭 <b>MAFIA — {html.escape(game.group_title)}</b>\n\n"
        f"👥 Игроков: <b>{len(game.players)}</b>\n"
        f"🟢 Живых: <b>{len(game.alive)}</b>\n\n"
        f"🌙 Ночь: <b>{game.night_number}</b>\n"
        f"☀️ День: <b>{game.day_number}</b>\n\n"
        f"<b>{get_phase_name(game.phase)}</b>"
    )


def get_phase_name(phase: str) -> str:
    return {
        "starting": "🎲 Распределение ролей",
        "night": "🌙 Город засыпает",
        "day_discussion": "💬 Обсуждение",
        "day_vote": "🗳 Голосование",
        "last_word": "🔴 Последнее слово",
        "finished": "🏆 Игра завершена",
        "stopped": "⏹ Игра остановлена",
    }.get(phase, phase)


def settings_keyboard():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    values = [2, 4, 6, 8, 10]
    rows = []
    rows.append([InlineKeyboardButton(text=f"💬 {v} мин.", callback_data=f"setting_discussion_{v}") for v in values[:3]])
    rows.append([InlineKeyboardButton(text=f"💬 {v} мин.", callback_data=f"setting_discussion_{v}") for v in values[3:]])
    rows.append([
        InlineKeyboardButton(text="🌙 Ночь −", callback_data="setting_night_minus"),
        InlineKeyboardButton(text="🌙 Ночь +", callback_data="setting_night_plus"),
    ])
    rows.append([
        InlineKeyboardButton(text="🔴 Последнее слово −", callback_data="setting_lastword_minus"),
        InlineKeyboardButton(text="🔴 Последнее слово +", callback_data="setting_lastword_plus"),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ НАЗАД", callback_data="settings_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_settings_text(game: Game) -> str:
    return (
        "⚙️ <b>НАСТРОЙКИ</b>\n\n"
        f"💬 Обсуждение: <b>{game.discussion_seconds // 60} мин.</b>\n"
        f"🌙 Ночь: <b>{game.night_seconds // 60} мин.</b>\n"
        f"🔴 Последнее слово: <b>{game.last_word_seconds} сек.</b>\n\n"
        "💬 Время обсуждения можно выбрать: <b>2 / 4 / 6 / 8 / 10 мин.</b>"
    )


async def setup_bot_avatar(bot: Bot):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_avatar.jpg")
    if not os.path.exists(path):
        return
    try:
        await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(path)))
    except Exception as error:
        print(f"⚠️ Не удалось установить аватар: {error}")


@dp.message(CommandStart())
async def start_handler(message: Message):
    bot = message.bot
    me = await bot.get_me()
    global BOT_USERNAME
    BOT_USERNAME = me.username

    # В группе /start не должен публиковать личный интерфейс.
    # Секретный UI разрешён только в личном чате с ботом.
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        game = games.get(message.chat.id)
        if game and (game.started or game.players):
            return
        welcome = await message.answer(
            get_welcome_text(message.chat.title or "MAFIA"),
            reply_markup=admin_start_keyboard(),
            parse_mode="HTML",
        )
        game_message_ids.setdefault(message.chat.id, set()).add(welcome.message_id)
        return

    # В личном чате /start открывает персональный интерфейс.
    argument = (message.text or "").split(maxsplit=1)[1] if " " in (message.text or "") else ""
    target_game = None
    if argument.startswith("game_"):
        try:
            target_game = games.get(int(argument.split("_", 1)[1]))
        except ValueError:
            pass

    await message.answer(
        "🎭 <b>MAFIA</b>\n\n"
        "Личный игровой интерфейс открыт.\n"
        "Секретные действия и результаты видны только вам.",
        reply_markup=private_bot_keyboard(me.username),
        parse_mode="HTML",
    )
    if target_game and target_game.started and message.from_user.id in target_game.alive and target_game.phase == "night":
        await send_current_private_action(bot, target_game, message.from_user.id)


@dp.callback_query(F.data == "private_launch")
async def private_launch_handler(callback: CallbackQuery, bot: Bot):
    me = await bot.get_me()
    await callback.message.edit_text(
        "🎭 <b>MAFIA</b>\n\nОткройте игру из кнопки «🎭 МОЙ НОЧНОЙ ХОД» в группе.",
        reply_markup=private_bot_keyboard(me.username), parse_mode="HTML")
    await callback.answer("▶️ Готово.")


@dp.callback_query(F.data == "private_restart")
async def private_restart_handler(callback: CallbackQuery, bot: Bot):
    await callback.answer("🔄 Рестарт выполняется администратором в группе.", show_alert=True)


@dp.my_chat_member()
async def bot_added_to_group(event: ChatMemberUpdated, bot: Bot):
    chat = event.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if event.new_chat_member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return
    if event.old_chat_member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return
    welcome = await bot.send_message(
        chat.id,
        get_welcome_text(chat.title or "MAFIA"),
        reply_markup=admin_start_keyboard(),
        parse_mode="HTML",
    )
    game_message_ids.setdefault(chat.id, set()).add(welcome.message_id)


@dp.callback_query(F.data == "create_game")
async def create_game_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message:
        return
    chat_id = callback.message.chat.id
    uid = callback.from_user.id
    if not await is_group_admin(bot, chat_id, uid):
        await callback.answer("⚠️ Только администратор группы.", show_alert=True); return
    if chat_id in games and games[chat_id].started:
        await callback.answer("⚠️ Игра уже идёт.", show_alert=True); return
    if chat_id in games and games[chat_id].players:
        await callback.answer("⚠️ Уже есть открытое лобби.", show_alert=True); return
    await cleanup_game_messages(bot, chat_id)
    game = Game(chat_id=chat_id, creator_id=uid, group_title=callback.message.chat.title or "MAFIA")
    games[chat_id] = game
    await callback.message.edit_text(get_lobby_text(game), reply_markup=lobby_keyboard(True, False), parse_mode="HTML")
    game_messages[chat_id] = callback.message.message_id
    game_message_ids.setdefault(chat_id, set()).add(callback.message.message_id)
    await callback.answer("🎭 Игровая комната создана!")


@dp.callback_query(F.data == "join_game")
async def join_game_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id; uid = callback.from_user.id
    game = games.get(chat_id)
    if not game or game.started:
        await callback.answer("❌ Сейчас нельзя присоединиться.", show_alert=True); return
    if uid in game.players:
        await callback.answer("🎭 Вы уже участвуете!", show_alert=True); return
    game.add_player(uid); game.player_names[uid] = get_player_name(callback.from_user)
    await callback.message.edit_text(get_lobby_text(game), reply_markup=lobby_keyboard(True, game.can_start()), parse_mode="HTML")
    await callback.answer("🎮 Вы присоединились!")


@dp.callback_query(F.data == "settings")
async def settings_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    game = games.get(chat_id)
    if not game: return
    await callback.message.edit_text(get_settings_text(game), reply_markup=settings_keyboard(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("setting_"))
async def setting_change_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    game = games.get(chat_id)
    if not game: return
    data = callback.data
    if data.startswith("setting_discussion_"):
        try: game.discussion_seconds = int(data.rsplit("_", 1)[1]) * 60
        except ValueError: pass
    elif data == "setting_night_minus": game.night_seconds = max(60, game.night_seconds - 60)
    elif data == "setting_night_plus": game.night_seconds = min(300, game.night_seconds + 60)
    elif data == "setting_lastword_minus": game.last_word_seconds = max(10, game.last_word_seconds - 10)
    elif data == "setting_lastword_plus": game.last_word_seconds = min(60, game.last_word_seconds + 10)
    await callback.message.edit_text(get_settings_text(game), reply_markup=settings_keyboard(), parse_mode="HTML")
    await callback.answer("⚙️ Настройка изменена.")


@dp.callback_query(F.data == "settings_back")
async def settings_back_handler(callback: CallbackQuery):
    if not callback.message: return
    game = games.get(callback.message.chat.id)
    if not game: return
    await callback.message.edit_text(get_lobby_text(game), reply_markup=lobby_keyboard(True, game.can_start()), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "start_game")
async def start_game_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id; game = games.get(chat_id)
    if not game: return
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    if not game.can_start():
        await callback.answer("❌ Нужно минимум 4 игрока.", show_alert=True); return
    if game.started:
        await callback.answer("🌙 Игра уже идёт.", show_alert=True); return
    game.start()
    await callback.message.edit_text(
        f"🎭 <b>ИГРА НАЧИНАЕТСЯ</b>\n\n👥 Игроков: <b>{len(game.players)}</b>\n\n🔒 Роли и ночные действия не публикуются.",
        reply_markup=admin_game_keyboard(chat_id, BOT_USERNAME), parse_mode="HTML")
    game_messages[chat_id] = callback.message.message_id
    game_message_ids.setdefault(chat_id, set()).add(callback.message.message_id)
    await callback.answer("🎭 Игра началась!")
    await start_game_task(bot, game)


async def start_game_task(bot: Bot, game: Game):
    old = game_tasks.get(game.chat_id)
    if old and not old.done(): old.cancel()
    game_tasks[game.chat_id] = asyncio.create_task(run_game(bot, game))


async def run_game(bot: Bot, game: Game):
    try:
        game.assign_roles()
        await send_game_message(bot, game, "🎲 <b>РОЛИ РАСПРЕДЕЛЕНЫ</b>\n\n🔒 Каждый игрок получил свою секретную роль.\n\n🌙 <b>ГОРОД ЗАСЫПАЕТ</b>", parse_mode="HTML")
        await asyncio.sleep(1)
        while game.started:
            winner = game.winner()
            if winner:
                await finish_game(bot, game, winner); return
            await run_night(bot, game)
            if not game.started: return
            winner = game.winner()
            if winner:
                await finish_game(bot, game, winner); return
            await run_day(bot, game)
            if not game.started: return
            winner = game.winner()
            if winner:
                await finish_game(bot, game, winner); return
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"❌ Ошибка игры {game.chat_id}: {error}")
        if game.started:
            await send_game_message(bot, game, "⚠️ Произошла техническая ошибка игрового процесса.", parse_mode="HTML")


async def send_current_private_action(bot: Bot, game: Game, user_id: int):
    role = game.roles.get(user_id)
    if role == MAFIA:
        targets = [(uid, game.player_names.get(uid, "Игрок")) for uid in game.alive if game.roles.get(uid) != MAFIA]
        await send_private_game_message(
            bot, game, user_id,
            "🔫 <b>ХОД МАФИИ</b>\n\nКого устранить?",
            reply_markup=target_keyboard(game.chat_id, targets, "mafia_target", game.mafia_votes.get(user_id)),
            parse_mode="HTML")
    elif role == DOCTOR:
        targets = [(uid, game.player_names.get(uid, "Игрок")) for uid in game.doctor_targets()]
        if targets:
            await send_private_game_message(
                bot, game, user_id,
                "💊 <b>ХОД ДОКТОРА</b>\n\nКого спасти?",
                reply_markup=target_keyboard(game.chat_id, targets, "doctor_target", game.doctor_target),
                parse_mode="HTML")
    elif role == COMMISSIONER:
        await send_private_game_message(
            bot, game, user_id,
            "🔎 <b>ХОД КОМИССАРА</b>\n\nВыберите действие.",
            reply_markup=night_action_keyboard(game.chat_id, role), parse_mode="HTML")


async def run_night(bot: Bot, game: Game):
    game.night_number += 1
    game.phase = "night"
    game.reset_night()
    await update_main_game_message(bot, game)
    await send_game_message(bot, game, f"🌙 <b>НОЧЬ {game.night_number}</b>\n\n🔒 Ночные действия выполняются приватно.", parse_mode="HTML")
    await run_mafia_phase(bot, game)
    if not game.started: return
    await run_doctor_phase(bot, game)
    if not game.started: return
    await run_commissioner_phase(bot, game)
    if not game.started: return
    await resolve_night(bot, game)


async def run_mafia_phase(bot: Bot, game: Game):
    mafia = game.alive_mafia()
    if not mafia: return
    for uid in mafia:
        targets = [(tid, game.player_names.get(tid, "Игрок")) for tid in game.alive if game.roles.get(tid) != MAFIA]
        try:
            await send_private_game_message(
                bot, game, uid,
                "🔫 <b>ХОД МАФИИ</b>\n\nКого устранить?",
                reply_markup=target_keyboard(game.chat_id, targets, "mafia_target", game.mafia_votes.get(uid)), parse_mode="HTML")
        except Exception:
            pass
    game.action_event.clear()
    try: await asyncio.wait_for(game.action_event.wait(), timeout=game.night_seconds)
    except asyncio.TimeoutError: pass
    for uid in mafia:
        if uid not in game.mafia_votes:
            targets = [tid for tid in game.alive if game.roles.get(tid) != MAFIA]
            if targets: game.mafia_votes[uid] = random.choice(targets)


async def run_doctor_phase(bot: Bot, game: Game):
    doctor = next((uid for uid in game.alive if game.roles.get(uid) == DOCTOR), None)
    if doctor is None: return
    targets = game.doctor_targets()
    if not targets: return
    try:
        await send_private_game_message(
            bot, game, doctor,
            "💊 <b>ХОД ДОКТОРА</b>\n\nКого спасти?",
            reply_markup=target_keyboard(game.chat_id, [(uid, game.player_names.get(uid, "Игрок")) for uid in targets], "doctor_target", game.doctor_target), parse_mode="HTML")
    except Exception: pass
    game.action_event.clear()
    try: await asyncio.wait_for(game.action_event.wait(), timeout=game.night_seconds)
    except asyncio.TimeoutError: pass
    if game.doctor_target is None:
        game.doctor_target = random.choice(targets)


async def run_commissioner_phase(bot: Bot, game: Game):
    commissioner = next((uid for uid in game.alive if game.roles.get(uid) == COMMISSIONER), None)
    if commissioner is None: return
    try:
        await send_private_game_message(
            bot, game, commissioner,
            "🔎 <b>ХОД КОМИССАРА</b>\n\nВыберите действие.",
            reply_markup=night_action_keyboard(game.chat_id, COMMISSIONER), parse_mode="HTML")
    except Exception: pass
    game.action_event.clear()
    try: await asyncio.wait_for(game.action_event.wait(), timeout=game.night_seconds)
    except asyncio.TimeoutError: pass
    if game.commissioner_target is None:
        targets = [uid for uid in game.alive if uid != commissioner]
        if targets: game.commissioner_target = random.choice(targets)


async def resolve_night(bot: Bot, game: Game):
    killed = None
    if game.mafia_votes:
        counts = Counter(game.mafia_votes.values())
        target, votes = counts.most_common(1)[0]
        if list(counts.values()).count(votes) == 1:
            killed = target
    saved = game.doctor_target
    if killed is not None and killed == saved:
        killed = None
    if killed is not None and killed in game.alive:
        game.alive.remove(killed)
    commissioner = next((uid for uid in game.players if game.roles.get(uid) == COMMISSIONER), None)
    commissioner_killed = None
    if commissioner is not None:
        commissioner_killed = game.commissioner_kill_target
        if commissioner_killed in game.alive and commissioner_killed != commissioner:
            game.alive.remove(commissioner_killed)
            if killed == commissioner_killed:
                killed = None
    if killed is None and commissioner_killed is None:
        await send_game_message(bot, game, "☀️ <b>УТРО</b>\n\n🌅 Ночь прошла спокойно.", parse_mode="HTML")
    else:
        names = []
        if killed is not None: names.append(safe_name(game, killed))
        if commissioner_killed is not None: names.append(safe_name(game, commissioner_killed))
        await send_game_message(bot, game, "☀️ <b>УТРО</b>\n\n🔴 Ночью погибли: " + ", ".join(names), parse_mode="HTML")
        for uid in [x for x in (killed, commissioner_killed) if x is not None]:
            await run_last_word(bot, game, uid)
    await update_main_game_message(bot, game)


async def run_last_word(bot: Bot, game: Game, user_id: int):
    if user_id not in game.players:
        return
    game.phase = "last_word"
    await update_main_game_message(bot, game)
    await send_game_message(bot, game, f"🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n🔴 {safe_name(game, user_id)} — последнее слово ({game.last_word_seconds} сек.).", parse_mode="HTML")
    game.last_word_user = user_id
    game.last_word_event.clear()
    try: await asyncio.wait_for(game.last_word_event.wait(), timeout=game.last_word_seconds)
    except asyncio.TimeoutError: pass
    game.last_word_user = None
    await update_main_game_message(bot, game)


async def commissioner_check_handler(callback: CallbackQuery, bot: Bot):
    try: _, chat_s, target_s = callback.data.split(":"); chat_id = int(chat_s); target_id = int(target_s)
    except Exception: await callback.answer("❌ Некорректная проверка.", show_alert=True); return
    game = games.get(chat_id); uid = callback.from_user.id
    if not game or game.phase != "night" or uid not in game.alive or game.roles.get(uid) != COMMISSIONER:
        await callback.answer("❌ Проверка недоступна.", show_alert=True); return
    if target_id not in game.alive or target_id == uid:
        await callback.answer("❌ Нельзя проверить этого игрока.", show_alert=True); return
    role = game.roles.get(target_id)
    await callback.answer(f"{ROLE_NAMES.get(role, 'Неизвестно')}", show_alert=True)
    game.commissioner_target = target_id


async def commissioner_kill_handler(callback: CallbackQuery, bot: Bot):
    try: _, chat_s, target_s = callback.data.split(":"); chat_id = int(chat_s); target_id = int(target_s)
    except Exception: await callback.answer("❌ Некорректная цель.", show_alert=True); return
    game = games.get(chat_id); uid = callback.from_user.id
    if not game or game.phase != "night" or uid not in game.alive or game.roles.get(uid) != COMMISSIONER:
        await callback.answer("❌ Убийство недоступно.", show_alert=True); return
    if target_id not in game.alive or target_id == uid:
        await callback.answer("❌ Нельзя выбрать этого игрока.", show_alert=True); return
    game.commissioner_kill_target = target_id
    game.action_event.set()
    if callback.message:
        await callback.message.delete()
    await send_private_game_message(bot, game, uid, f"☠️ <b>ЦЕЛЬ ВЫБРАНА</b>\n\n{safe_name(game, target_id)}", parse_mode="HTML")


@dp.callback_query(F.data.startswith("mafia_target:"))
async def mafia_target_handler(callback: CallbackQuery, bot: Bot):
    await handle_private_target(callback, MAFIA)


@dp.callback_query(F.data.startswith("doctor_target:"))
async def doctor_target_handler(callback: CallbackQuery, bot: Bot):
    await handle_private_target(callback, DOCTOR)


async def handle_private_target(callback: CallbackQuery, role: str):
    try: _, chat_s, target_s = callback.data.split(":"); chat_id = int(chat_s); target_id = int(target_s)
    except Exception: await callback.answer("❌ Некорректная кнопка.", show_alert=True); return
    game = games.get(chat_id); uid = callback.from_user.id
    if not game or game.phase != "night" or uid not in game.alive or game.roles.get(uid) != role:
        await callback.answer("❌ Действие недоступно.", show_alert=True); return
    if target_id not in game.alive:
        await callback.answer("❌ Игрок уже мёртв.", show_alert=True); return
    if role == MAFIA and game.roles.get(target_id) == MAFIA:
        await callback.answer("❌ Нельзя выбрать союзника.", show_alert=True); return
    if role == DOCTOR and target_id in game.doctor_healed:
        await callback.answer("❌ Этого игрока уже лечили в этой игре.", show_alert=True); return
    if role == MAFIA: game.mafia_votes[uid] = target_id
    else: game.doctor_target = target_id
    name = safe_name(game, target_id)
    if callback.message:
        await callback.message.delete()
    await callback.message.answer(("🔫 <b>ВЫ УБИЛИ</b>\n\n" if role == MAFIA else "💊 <b>ВЫ ВЫЛЕЧИЛИ</b>\n\n") + name, parse_mode="HTML")
    if role == MAFIA:
        if len(game.mafia_votes) >= len(game.alive_mafia()): game.action_event.set()
    else:
        game.action_event.set()


@dp.callback_query(F.data.startswith("night_commissioner:"))
async def open_commissioner_action(callback: CallbackQuery, bot: Bot):
    try: chat_id = int(callback.data.split(":")[1])
    except Exception: await callback.answer("❌ Некорректная игра.", show_alert=True); return
    game = games.get(chat_id); uid = callback.from_user.id
    if not game or game.phase != "night" or uid not in game.alive or game.roles.get(uid) != COMMISSIONER:
        await callback.answer("❌ Действие недоступно.", show_alert=True); return
    targets = [(tid, game.player_names.get(tid, "Игрок")) for tid in game.alive if tid != uid]
    if callback.message: await callback.message.delete()
    await send_private_game_message(bot, game, uid, "🔎 <b>КОГО ПРОВЕРИТЬ?</b>", reply_markup=target_keyboard(chat_id, targets, "commissioner_target", game.commissioner_target), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("night_commissioner_kill:"))
async def open_commissioner_kill(callback: CallbackQuery, bot: Bot):
    try: chat_id = int(callback.data.split(":")[1])
    except Exception: await callback.answer("❌ Некорректная игра.", show_alert=True); return
    game = games.get(chat_id); uid = callback.from_user.id
    if not game or game.phase != "night" or uid not in game.alive or game.roles.get(uid) != COMMISSIONER:
        await callback.answer("❌ Действие недоступно.", show_alert=True); return
    targets = [(tid, game.player_names.get(tid, "Игрок")) for tid in game.alive if tid != uid]
    if callback.message: await callback.message.delete()
    await send_private_game_message(bot, game, uid, "☠️ <b>КОГО УБИТЬ?</b>", reply_markup=target_keyboard(chat_id, targets, "commissioner_kill_target", game.commissioner_kill_target), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("commissioner_target:"))
async def commissioner_target_handler(callback: CallbackQuery, bot: Bot):
    await commissioner_check_handler(callback, bot)


@dp.callback_query(F.data.startswith("commissioner_kill_target:"))
async def commissioner_kill_target_handler(callback: CallbackQuery, bot: Bot):
    await commissioner_kill_handler(callback, bot)


@dp.callback_query(F.data.startswith("cancel_action:"))
async def cancel_action_handler(callback: CallbackQuery):
    await callback.answer("❌ Выбор отменён.")


@dp.callback_query(F.data.startswith("my_role:"))
async def my_role_handler(callback: CallbackQuery, bot: Bot):
    try: chat_id = int(callback.data.split(":")[1])
    except Exception: await callback.answer("❌ Некорректная игра.", show_alert=True); return
    game = games.get(chat_id)
    if not game or callback.from_user.id not in game.players:
        await callback.answer("🔵 Вы наблюдатель.", show_alert=True); return
    role = game.roles.get(callback.from_user.id)
    if not role:
        await callback.answer("❌ Роль ещё не распределена.", show_alert=True); return
    await callback.answer(f"{ROLE_NAMES[role]}\n\n{ROLE_DESCRIPTIONS[role]}", show_alert=True)


async def update_main_game_message(bot: Bot, game: Game):
    message_id = game_messages.get(game.chat_id)
    if not message_id: return
    try:
        await bot.edit_message_text(
            chat_id=game.chat_id,
            message_id=message_id,
            text=get_game_status_text(game) if game.started else get_welcome_text(game.group_title),
            reply_markup=admin_game_keyboard(game.chat_id, BOT_USERNAME) if game.started else admin_start_keyboard(),
            parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("vote:"))
async def vote_handler(callback: CallbackQuery):
    try: _, chat_s, target_s = callback.data.split(":"); chat_id = int(chat_s); target_id = int(target_s)
    except Exception: await callback.answer("❌ Некорректное голосование.", show_alert=True); return
    game = games.get(chat_id); voter = callback.from_user.id
    if not game or game.phase != "day_vote" or voter not in game.alive or target_id not in game.alive or target_id == voter:
        await callback.answer("❌ Голосование недоступно.", show_alert=True); return
    game.day_votes[voter] = target_id
    if callback.message:
        try:
            players = [(uid, game.player_names.get(uid, "Игрок")) for uid in (game.tie_candidates or game.alive_players())]
            await callback.message.edit_reply_markup(reply_markup=vote_keyboard(chat_id, players, target_id))
        except Exception: pass
    await callback.answer(f"🗳 Вы выбрали {game.player_names.get(target_id,'Игрок')}")
    if game.all_day_votes_complete(): game.action_event.set()


async def run_day(bot: Bot, game: Game):
    game.day_number += 1; game.phase = "day_discussion"
    await update_main_game_message(bot, game)
    await send_game_message(bot, game, f"☀️ <b>ДЕНЬ</b>\n\n💬 <b>ОБСУЖДЕНИЕ</b>\n\nОбсуждение длится <b>{game.discussion_seconds // 60} мин.</b>", parse_mode="HTML")
    await asyncio.sleep(game.discussion_seconds)
    if not game.started: return
    await conduct_vote(bot, game, None)


async def conduct_vote(bot: Bot, game: Game, candidates: list[int] | None):
    game.phase = "day_vote"; game.reset_day_votes()
    alive = game.alive_players()
    ids = candidates if candidates is not None else alive
    players = [(uid, game.player_names.get(uid, "Игрок")) for uid in ids if uid in game.alive]
    await update_main_game_message(bot, game)
    await send_game_message(bot, game, "🗳 <b>КАК ВЫ ДУМАЕТЕ, КТО МАФИЯ?</b>", reply_markup=vote_keyboard(game.chat_id, players), parse_mode="HTML")
    game.action_event.clear()
    try: await asyncio.wait_for(game.action_event.wait(), timeout=game.vote_seconds)
    except asyncio.TimeoutError: pass
    alive = game.alive_players()
    for voter in alive:
        if voter not in game.day_votes:
            choices = [uid for uid in ids if uid in game.alive and uid != voter]
            if choices: game.day_votes[voter] = random.choice(choices)
    await publish_vote_results(bot, game)
    counts = Counter(game.day_votes.values())
    if not counts: return
    high = max(counts.values()); leaders = [uid for uid, c in counts.items() if c == high]
    if len(leaders) > 1 and candidates is None:
        game.tie_candidates = leaders
        await send_game_message(bot, game, "⚖️ <b>НИЧЬЯ</b>\n\nРешающее голосование между двумя игроками.", parse_mode="HTML")
        await conduct_vote(bot, game, leaders)
        return
    if len(leaders) > 1:
        await send_game_message(bot, game, "⚖️ <b>СНОВА НИЧЬЯ</b>\n\nНикто не изгнан.", parse_mode="HTML")
        return
    eliminated = leaders[0]
    if eliminated in game.alive: game.alive.remove(eliminated)
    await send_game_message(bot, game, f"🔴 <b>{safe_name(game, eliminated)}</b> покидает игру.\n\n🔴 Последнее слово.", parse_mode="HTML")
    await run_last_word(bot, game, eliminated)


async def publish_vote_results(bot: Bot, game: Game):
    lines = ["🗳 <b>РЕЗУЛЬТАТЫ ГОЛОСОВАНИЯ</b>", ""]
    for voter, target in game.day_votes.items():
        lines.append(f"{safe_name(game, voter)} → {safe_name(game, target)}")
    lines.append("")
    counts = Counter(game.day_votes.values())
    for uid, count in counts.most_common():
        lines.append(f"{safe_name(game, uid)} — <b>{count}</b>")
    await send_game_message(bot, game, "\n".join(lines), parse_mode="HTML")


async def finish_game(bot: Bot, game: Game, winner: str):
    game.started = False; game.phase = "finished"; game.action_event.set()
    await update_main_game_message(bot, game)
    title = "🔫 <b>МАФИЯ ПОБЕДИЛА!</b>" if winner == "mafia" else "🏆 <b>ГОРОД ПОБЕДИЛ!</b>"
    await send_game_message(bot, game, f"🏆 <b>ИГРА ОКОНЧЕНА</b>\n\n{title}\n\nСыграем ещё раз?", reply_markup=postgame_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "postgame_new")
async def postgame_new_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    old = games.get(chat_id)
    if not old: return
    await cleanup_game_messages(bot, chat_id)
    old.reset_to_lobby(False)
    lobby = await bot.send_message(chat_id, get_lobby_text(old), reply_markup=lobby_keyboard(True, False), parse_mode="HTML")
    game_messages[chat_id] = lobby.message_id; game_message_ids.setdefault(chat_id, set()).add(lobby.message_id)
    await callback.answer("🎮 Новое лобби готово.")


@dp.callback_query(F.data == "postgame_end")
async def postgame_end_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    game = games.get(chat_id)
    if game: game.reset_to_lobby(False)
    await cleanup_game_messages(bot, chat_id)
    welcome = await bot.send_message(chat_id, get_welcome_text(callback.message.chat.title or "MAFIA"), reply_markup=admin_start_keyboard(), parse_mode="HTML")
    game_messages[chat_id] = welcome.message_id
    game_message_ids.setdefault(chat_id, set()).add(welcome.message_id)
    await callback.answer("🏁 Игра завершена.")


@dp.callback_query(F.data == "admin_stop")
async def admin_stop_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    game = games.get(chat_id)
    if game: game.stop()
    task = game_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    await cleanup_game_messages(bot, chat_id)
    welcome = await bot.send_message(chat_id, get_welcome_text(callback.message.chat.title or "MAFIA"), reply_markup=admin_start_keyboard(), parse_mode="HTML")
    game_messages[chat_id] = welcome.message_id; game_message_ids.setdefault(chat_id, set()).add(welcome.message_id)
    await callback.answer("⏹ Игра остановлена.")


@dp.callback_query(F.data == "admin_restart")
async def admin_restart_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    game = games.get(chat_id)
    if not game or not game.can_start():
        await callback.answer("❌ Недостаточно игроков.", show_alert=True); return
    task = game_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    await cleanup_game_messages(bot, chat_id)
    game.restart()
    restart_message = await bot.send_message(
        chat_id,
        f"🔄 <b>ИГРА ПЕРЕЗАПУСКАЕТСЯ</b>\n\n👥 Игроков: <b>{len(game.players)}</b>\n\n🎲 Роли будут распределены заново.",
        reply_markup=admin_game_keyboard(chat_id, BOT_USERNAME), parse_mode="HTML")
    game_messages[chat_id] = restart_message.message_id
    game_message_ids.setdefault(chat_id, set()).add(restart_message.message_id)
    await callback.answer("🔄 Игра перезапускается.")
    await start_game_task(bot, game)


@dp.callback_query(F.data == "admin_new_game")
async def admin_new_game_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id = callback.message.chat.id
    if not await is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор.", show_alert=True); return
    task = game_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    await cleanup_game_messages(bot, chat_id)
    game = Game(chat_id=chat_id, creator_id=callback.from_user.id, group_title=callback.message.chat.title or "MAFIA")
    games[chat_id] = game
    await callback.message.edit_text(get_lobby_text(game), reply_markup=lobby_keyboard(True, False), parse_mode="HTML")
    game_messages[chat_id] = callback.message.message_id; game_message_ids.setdefault(chat_id, set()).add(callback.message.message_id)
    await callback.answer("🆕 Новое лобби создано.")


@dp.callback_query(F.data.startswith("my_night:"))
async def my_night_handler(callback: CallbackQuery, bot: Bot):
    try: chat_id = int(callback.data.split(":")[1])
    except Exception: await callback.answer("❌ Некорректная игра.", show_alert=True); return
    game = games.get(chat_id)
    if not game or not game.started or game.phase != "night":
        await callback.answer("🌅 Сейчас нет ночного хода.", show_alert=True); return
    await callback.answer("🔐 Откройте личный чат с ботом через кнопку «🎭 МОЙ НОЧНОЙ ХОД».", show_alert=True)


async def main():
    bot = Bot(BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_bot_avatar(bot)
    me = await bot.get_me()
    global BOT_USERNAME
    BOT_USERNAME = me.username
    print(f"🤖 @{BOT_USERNAME} запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

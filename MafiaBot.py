import asyncio
import os
import html
import random
import time
import traceback
from collections import Counter

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (Message, CallbackQuery, ChatMemberUpdated, FSInputFile, InputProfilePhotoStatic,
    InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats)
from aiogram.enums import ChatType, ChatMemberStatus
from dotenv import load_dotenv

from game import Game, MAFIA, DOCTOR, COMMISSIONER, ROLE_NAMES, ROLE_DESCRIPTIONS
from keyboards import (
    lobby_keyboard, admin_start_keyboard, admin_game_keyboard,
    target_keyboard, vote_keyboard, night_action_keyboard,
    postgame_keyboard,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ Не найден BOT_TOKEN в файле .env")

dp = Dispatcher()
games: dict[int, Game] = {}
game_messages: dict[int, int] = {}
game_message_ids: dict[int, set[int]] = {}
private_message_ids: dict[int, set[int]] = {}
game_tasks: dict[int, asyncio.Task] = {}
BOT_USERNAME: str | None = None


async def send_game_message(bot: Bot, game: Game, text: str, **kwargs):
    message = await bot.send_message(game.chat_id, text, **kwargs)
    game_message_ids.setdefault(game.chat_id, set()).add(message.message_id)
    return message


async def send_private_game_message(bot: Bot, game: Game, user_id: int, text: str, **kwargs):
    message = await bot.send_message(user_id, text, **kwargs)
    private_message_ids.setdefault(game.chat_id, set()).add(message.message_id)
    return message


async def cleanup_private_messages(bot: Bot, chat_id: int):
    # Telegram позволяет боту удалять свои сообщения в личном чате.
    for message_id in set(private_message_ids.pop(chat_id, set())):
        try:
            # В private chat message_id alone is sufficient only together with user chat id;
            # therefore private ids are additionally stored as encoded pairs below in practice.
            pass
        except Exception:
            pass


async def delete_message_safe(bot: Bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# Private messages are stored as (user_id, message_id) pairs encoded in a set.
private_pairs: dict[int, set[tuple[int, int]]] = {}


async def send_private_game_message(bot: Bot, game: Game, user_id: int, text: str, **kwargs):
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
    # Всегда используем имя, а не username.
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
        InlineKeyboardButton(text="🔴 Последнее слово −", callback_data="setting_lastword_minus"),
        InlineKeyboardButton(text="🔴 Последнее слово +", callback_data="setting_lastword_plus"),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ НАЗАД", callback_data="settings_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_settings_text(game: Game) -> str:
    discussion = (f"{game.discussion_seconds} сек." if game.discussion_seconds < 60
                  else f"{game.discussion_seconds // 60} мин.")
    return (
        "⚙️ <b>НАСТРОЙКИ</b>\n\n"
        f"💬 Обсуждение: <b>{discussion}</b>\n"
        f"🌙 Ночь: <b>15 сек.</b>\n"
        f"🔴 Последнее слово: <b>{game.last_word_seconds} сек.</b>\n"
        "💬 После рассвета по умолчанию идёт обсуждение <b>30 сек.</b>, затем голосование."
    )


async def setup_bot_avatar(bot: Bot):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_avatar.jpg")
    if not os.path.exists(path):
        return
    try:
        await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(path)))
    except Exception as error:
        print(f"⚠️ Не удалось установить аватар: {error}")



# =========================
# COMMANDS: GROUP / PRIVATE
# =========================


async def create_lobby_for_group(bot: Bot, message: Message):
    chat_id = message.chat.id
    uid = message.from_user.id
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    if not await is_group_admin(bot, chat_id, uid):
        await message.reply("⚠️ <b>Только администратор группы может начать игру.</b>", parse_mode="HTML")
        return False
    current = games.get(chat_id)
    if current and current.started:
        await message.reply("⚠️ <b>Игра уже идёт.</b>", parse_mode="HTML")
        return False
    if current and current.players:
        await message.reply("⚠️ <b>Лобби уже открыто.</b>\n\nПрисоединяйтесь через кнопку <b>🎮 Я ИГРАЮ</b>.", parse_mode="HTML")
        return False
    await cleanup_game_messages(bot, chat_id)
    game = Game(chat_id=chat_id, creator_id=uid, group_title=message.chat.title or "MAFIA")
    games[chat_id] = game
    lobby = await bot.send_message(chat_id, get_lobby_text(game), reply_markup=lobby_keyboard(True, False), parse_mode="HTML")
    game_messages[chat_id] = lobby.message_id
    game_message_ids.setdefault(chat_id, set()).add(lobby.message_id)
    return True

@dp.message(Command("mafia"))
async def mafia_command(message: Message, bot: Bot):
    await create_lobby_for_group(bot, message)

@dp.message(Command("stop"))
async def stop_command(message: Message, bot: Bot):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("⏹ Личный режим остановлен.")
        return
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    chat_id = message.chat.id
    if not await is_group_admin(bot, chat_id, message.from_user.id):
        await message.reply("⚠️ <b>Только администратор группы.</b>", parse_mode="HTML")
        return
    task = game_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
    game = games.get(chat_id)
    if game:
        game.stop()
    await cleanup_game_messages(bot, chat_id)
    games.pop(chat_id, None)
    welcome = await bot.send_message(chat_id, get_welcome_text(message.chat.title or "MAFIA"), reply_markup=admin_start_keyboard(), parse_mode="HTML")
    game_messages[chat_id] = welcome.message_id
    game_message_ids.setdefault(chat_id, set()).add(welcome.message_id)

@dp.message(Command("restart"))
async def restart_command(message: Message, bot: Bot):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    chat_id = message.chat.id
    if not await is_group_admin(bot, chat_id, message.from_user.id):
        await message.reply("⚠️ <b>Только администратор группы.</b>", parse_mode="HTML")
        return
    game = games.get(chat_id)
    if not game:
        await message.reply("❌ Нет игры. Используйте <b>/mafia</b>.", parse_mode="HTML")
        return
    if not game.can_start():
        await message.reply("❌ Для запуска нужно минимум <b>4 игрока</b>.", parse_mode="HTML")
        return
    task = game_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
    await cleanup_game_messages(bot, chat_id)
    game.restart()
    restart_message = await bot.send_message(chat_id, f"🔄 <b>ИГРА ПЕРЕЗАПУСКАЕТСЯ</b>\n\n👥 Игроков: <b>{len(game.players)}</b>\n\n🎲 Роли будут распределены заново.", reply_markup=admin_game_keyboard(chat_id, BOT_USERNAME), parse_mode="HTML")
    game_messages[chat_id] = restart_message.message_id
    game_message_ids.setdefault(chat_id, set()).add(restart_message.message_id)
    await start_game_task(bot, game)

@dp.message(Command("reset"))
async def reset_command(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("♻️ <b>Личный режим сброшен.</b>\n\nНажмите /start для повторной активации.", parse_mode="HTML")

@dp.message(CommandStart())
async def start_handler(message: Message):
    bot = message.bot
    me = await bot.get_me()
    global BOT_USERNAME
    BOT_USERNAME = me.username
    argument = (message.text or "").split(maxsplit=1)[1] if " " in (message.text or "") else ""
    target_game = None
    if argument.startswith("game_"):
        try:
            target_game = games.get(int(argument.split("_", 1)[1]))
        except ValueError:
            pass
    if target_game and target_game.started and message.from_user.id in target_game.alive and target_game.phase == "night":
        await send_current_private_action(bot, target_game, message.from_user.id)
        return
    # Обычный /start не открывает служебное окно между ходами.
    # Ночной интерфейс открывается только через deep-link game_<chat_id>.
    return


@dp.my_chat_member()
async def bot_added_to_group(event: ChatMemberUpdated, bot: Bot):
    chat = event.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if event.new_chat_member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return
    if event.old_chat_member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return
    await bot.send_message(chat.id, get_welcome_text(chat.title or "MAFIA"), reply_markup=admin_start_keyboard(), parse_mode="HTML")


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
    elif data in ("setting_lastword_minus", "setting_lastword_plus"):
        game.last_word_seconds = 10
    game.night_seconds = 15
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
    game.assign_roles()
    await callback.message.edit_text(
        "🎭 <b>РОЛИ РАСПРЕДЕЛЕНЫ</b>",
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
        # Никогда не скрываем реальную причину в логах Bothost.
        # Пользовательское сообщение остаётся нейтральным, чтобы не ломать UI игры.
        print(f"❌ Ошибка игрового процесса {game.chat_id}: {type(error).__name__}: {error}")
        traceback.print_exc()
        if game.started:
            try:
                await send_game_message(
                    bot, game,
                    "⚠️ Произошла техническая ошибка игрового процесса.\n\n"
                    "🔧 Подробности записаны в журнал бота.",
                    parse_mode="HTML"
                )
            except Exception as notify_error:
                print(f"❌ Не удалось отправить сообщение об ошибке: {type(notify_error).__name__}: {notify_error}")
                traceback.print_exc()


async def show_role_alert(callback: CallbackQuery, game: Game, user_id: int):
    role = game.roles.get(user_id)
    if not role:
        await callback.answer("❌ У вас нет роли.", show_alert=True); return
    await callback.answer(f"{ROLE_NAMES[role]}\n\n{ROLE_DESCRIPTIONS[role]}", show_alert=True)


@dp.callback_query(F.data.startswith("my_role:"))
async def my_role_handler(callback: CallbackQuery, bot: Bot):
    try: chat_id = int(callback.data.split(":")[1])
    except Exception: await callback.answer("❌ Некорректная игра.", show_alert=True); return
    game = games.get(chat_id)
    if not game or callback.from_user.id not in game.players:
        await callback.answer("🔵 Вы наблюдатель.", show_alert=True); return
    await show_role_alert(callback, game, callback.from_user.id)


async def private_target_message(bot: Bot, game: Game, user_id: int, title: str, targets, prefix: str, selected_id=None):
    return await send_private_game_message(
        bot, game, user_id, title,
        reply_markup=target_keyboard(game.chat_id, targets, prefix, selected_id), parse_mode="HTML")


async def send_current_private_action(bot: Bot, game: Game, user_id: int):
    """Open the private action UI for the player's current night role."""
    role = game.roles.get(user_id)
    if role == MAFIA:
        targets = [(uid, game.player_names.get(uid, "Игрок"))
                   for uid in game.alive if game.roles.get(uid) != MAFIA]
        await send_private_game_message(
            bot, game, user_id,
            "🔫 <b>ХОД МАФИИ</b>\n\nВыберите игрока, которого хотите убить.",
            reply_markup=target_keyboard(game.chat_id, targets, "mafia_target", game.mafia_votes.get(user_id)),
            parse_mode="HTML")
    elif role == DOCTOR:
        targets = [(uid, game.player_names.get(uid, "Игрок"))
                   for uid in game.doctor_targets()]
        if targets:
            await send_private_game_message(
                bot, game, user_id,
                "💊 <b>ХОД ДОКТОРА</b>\n\nВыберите игрока, которого хотите лечить.",
                reply_markup=target_keyboard(game.chat_id, targets, "doctor_target", game.doctor_target),
                parse_mode="HTML")
    elif role == COMMISSIONER:
        await send_private_game_message(
            bot, game, user_id,
            "🔎 <b>ХОД КОМИССАРА</b>\n\nВыберите одно действие.",
            reply_markup=night_action_keyboard(game.chat_id, role),
            parse_mode="HTML")



async def _edit_timer_message(bot: Bot, chat_id: int, message_id: int, text: str, reply_markup=None):
    """Редактирует таймер независимо от основного игрового цикла."""
    try:
        await asyncio.wait_for(
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            ),
            timeout=0.5,
        )
    except asyncio.TimeoutError:
        print("⚠️ Таймер: Telegram не ответил за 0.5 сек.; отсчёт продолжается.")
    except Exception as error:
        print(f"⚠️ Таймер: {type(error).__name__}: {error}")


async def run_reliable_timer(bot: Bot, game: Game, message_id: int, deadline: float, text_builder, reply_markup_builder=None):
    """Таймер с абсолютным дедлайном. Сетевые задержки не останавливают тик."""
    loop = asyncio.get_running_loop()
    next_tick = loop.time()
    last_remaining = None
    pending_edit = None
    try:
        while game.started:
            now = time.monotonic()
            remaining = max(0, int(deadline - now + 0.999))
            if remaining != last_remaining:
                # Отправляем редактирование отдельной задачей: даже если Telegram
                # зависнет/замедлится, сам отсчёт продолжает идти по monotonic clock.
                if pending_edit is None or pending_edit.done():
                    pending_edit = asyncio.create_task(
                        _edit_timer_message(
                            bot, game.chat_id, message_id,
                            text_builder(remaining),
                            reply_markup_builder(remaining) if reply_markup_builder else bot_chat_keyboard()
                        )
                    )
                    pending_edit.add_done_callback(_log_background_task_error)
                last_remaining = remaining
            if remaining <= 0:
                break
            next_tick += 0.1
            delay = max(0.01, next_tick - loop.time())
            await asyncio.sleep(delay)
    finally:
        if pending_edit is not None and not pending_edit.done():
            try:
                await asyncio.wait_for(pending_edit, timeout=0.6)
            except Exception:
                pending_edit.cancel()

async def run_night(bot: Bot, game: Game):
    """Единое ночное окно для всех живых ночных ролей."""
    game.night_number += 1
    game.phase = "night"
    game.reset_night_actions()
    await update_main_game_message(bot, game)

    seconds = max(0, int(game.night_seconds))
    night_message = await send_game_message(
        bot,
        game,
        f"🌙 <b>НАСТУПИЛА НОЧЬ</b>\n\n😴 <b>ВСЕ ИГРОКИ СПЯТ</b>\n\n⏱ <b>{seconds:02d} сек.</b>",
        reply_markup=bot_chat_keyboard(),
        parse_mode="HTML",
    )

    deadline = time.monotonic() + seconds
    timer_task = asyncio.create_task(
        run_reliable_timer(
            bot,
            game,
            night_message.message_id,
            deadline,
            lambda remaining: (
                f"🌙 <b>НАСТУПИЛА НОЧЬ</b>\n\n"
                f"😴 <b>ВСЕ ИГРОКИ СПЯТ</b>\n\n"
                f"⏱ <b>{remaining:02d} сек.</b>"
            ),
        )
    )
    timer_task.add_done_callback(_log_background_task_error)

    try:
        # Все ночные роли получают личный ход одновременно.
        tasks = []
        for uid in list(game.alive):
            role = game.roles.get(uid)
            if role in (MAFIA, DOCTOR, COMMISSIONER):
                tasks.append(send_current_private_action(bot, game, uid))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    print(f"⚠️ Не удалось выдать ночной ход: {type(result).__name__}: {result}")

        if game.night_actions_complete():
            game.action_event.set()

        remaining = max(0.0, deadline - time.monotonic())
        if remaining > 0 and not game.night_actions_complete():
            try:
                await asyncio.wait_for(game.action_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass

        # Если кто-то не сделал ход, выбираем безопасное значение автоматически,
        # чтобы игровой цикл гарантированно продолжился. Это не бот-игрок: это
        # только страховка от незавершённого действия человека.
        for uid in game.alive_mafia():
            if uid not in game.mafia_votes:
                targets = [tid for tid in game.alive if game.roles.get(tid) != MAFIA]
                if targets:
                    game.mafia_votes[uid] = random.choice(targets)

        doctor = next((uid for uid in game.alive if game.roles.get(uid) == DOCTOR), None)
        if doctor is not None and game.doctor_target is None:
            targets = game.doctor_targets()
            if targets:
                game.doctor_target = random.choice(targets)

        commissioner = next((uid for uid in game.alive if game.roles.get(uid) == COMMISSIONER), None)
        if commissioner is not None and game.commissioner_target is None and game.commissioner_kill_target is None:
            targets = [uid for uid in game.alive if uid != commissioner]
            if targets:
                game.commissioner_target = random.choice(targets)
    finally:
        if not timer_task.done():
            timer_task.cancel()
            try:
                await timer_task
            except asyncio.CancelledError:
                pass

    if not game.started:
        return

    deaths = resolve_night(game)

    if game.mafia_kill_target is not None and game.mafia_kill_target in deaths:
        await send_game_message(bot, game, f"🔴 <b>{safe_name(game, game.mafia_kill_target)}</b> убит(а) мафией.", parse_mode="HTML")
    if game.commissioner_kill_target is not None and game.commissioner_kill_target in deaths:
        await send_game_message(bot, game, f"🔴 <b>{safe_name(game, game.commissioner_kill_target)}</b> убит(а) комиссаром.", parse_mode="HTML")

    # Все результаты ночных действий приходят обычными личными сообщениями,
    # без кнопки «Показать уведомление» и без callback-alert.
    if game.doctor_target is not None:
        try:
            await send_private_game_message(
                bot, game, game.doctor_target,
                "💊 <b>ВАС ВЫЛЕЧИЛИ</b>\n\nЭтой ночью доктор выбрал вас для лечения.",
                parse_mode="HTML",
            )
        except Exception as error:
            print(f"⚠️ Не удалось отправить сообщение о лечении: {type(error).__name__}: {error}")

    if game.mafia_kill_target is not None and game.mafia_kill_target in deaths:
        try:
            await send_private_game_message(
                bot, game, game.mafia_kill_target,
                "🔫 <b>ВАС УБИЛА МАФИЯ</b>\n\nЭтой ночью мафия выбрала вас своей жертвой.",
                parse_mode="HTML",
            )
        except Exception as error:
            print(f"⚠️ Не удалось отправить сообщение о смерти: {type(error).__name__}: {error}")

    if game.commissioner_kill_target is not None and game.commissioner_kill_target in deaths:
        try:
            await send_private_game_message(
                bot, game, game.commissioner_kill_target,
                "☠️ <b>ВАС УБИЛ КОМИССАР</b>\n\nЭтой ночью комиссар выбрал вас своей целью.",
                parse_mode="HTML",
            )
        except Exception as error:
            print(f"⚠️ Не удалось отправить сообщение об убийстве комиссара: {type(error).__name__}: {error}")

    # Результат проверки комиссара остаётся секретным и приходит только ему сообщением.
    if game.commissioner_target is not None:
        checked = game.commissioner_target
        result = {
            MAFIA: "🔴 МАФИЯ",
            DOCTOR: "💊 ДОКТОР",
            COMMISSIONER: "🔎 КОМИССАР",
        }.get(game.roles.get(checked), "🟢 МИРНЫЙ ЖИТЕЛЬ")
        commissioner = next((uid for uid in game.alive if game.roles.get(uid) == COMMISSIONER), None)
        if commissioner is not None:
            try:
                await send_private_game_message(
                    bot, game, commissioner,
                    f"🔎 <b>РЕЗУЛЬТАТ ПРОВЕРКИ</b>\n\n"
                    f"Игрок: <b>{safe_name(game, checked)}</b>\n"
                    f"Результат: <b>{result}</b>",
                    parse_mode="HTML",
                )
            except Exception as error:
                print(f"⚠️ Не удалось отправить результат проверки: {type(error).__name__}: {error}")

    if deaths:
        await send_game_message(
            bot, game,
            "☀️ <b>ГОРОД ПРОСЫПАЕТСЯ</b>\n\nНочью погибли: "
            + ", ".join(safe_name(game, uid) for uid in deaths),
            reply_markup=bot_chat_keyboard(), parse_mode="HTML",
        )
        for killed in list(deaths):
            if not game.started:
                return
            await send_game_message(
                bot, game, f"🔴 <b>{safe_name(game, killed)}</b> получает последнее слово.",
                reply_markup=bot_chat_keyboard(), parse_mode="HTML",
            )
            await run_last_word(bot, game, killed)
    else:
        await send_game_message(
            bot, game,
            "☀️ <b>ГОРОД ПРОСЫПАЕТСЯ</b>\n\nЭтой ночью никто не погиб.",
            reply_markup=bot_chat_keyboard(), parse_mode="HTML",
        )

def resolve_night(game: Game) -> list[int]:
    deaths = set()
    game.mafia_kill_target = None
    if game.mafia_votes:
        counts = Counter(game.mafia_votes.values())
        high = max(counts.values())
        game.mafia_kill_target = random.choice([uid for uid, c in counts.items() if c == high])
        deaths.add(game.mafia_kill_target)
    if game.commissioner_kill_target is not None:
        deaths.add(game.commissioner_kill_target)
    # Лечение отменяет ночное убийство.
    if game.doctor_target in deaths:
        deaths.remove(game.doctor_target)
    deaths = {uid for uid in deaths if uid in game.alive}
    for uid in deaths:
        game.alive.discard(uid)
    if game.doctor_target is not None:
        game.doctor_healed.add(game.doctor_target)
    return list(deaths)


def _log_background_task_error(task: asyncio.Task):
    """Log exceptions from detached timers instead of losing them silently."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        print(f"❌ Ошибка фонового таймера: {type(error).__name__}: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)


async def run_last_word(bot: Bot, game: Game, player_id: int):
    """Открыть полноценное 10-секундное окно последнего слова."""
    game.last_word_player = player_id
    game.last_word_text = None
    game.active_last_words.add(player_id)

    message = await send_game_message(
        bot,
        game,
        f"🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n"
        f"<b>{safe_name(game, player_id)}</b> может написать последнее сообщение.\n\n"
        f"⏱ <b>{game.last_word_seconds} сек.</b>",
        reply_markup=bot_chat_keyboard(),
        parse_mode="HTML",
    )

    # Не запускаем следующий этап, пока 10-секундное окно не закончится.
    await _last_word_timer(bot, game, player_id, message.message_id)


async def _last_word_timer(bot: Bot, game: Game, player_id: int, message_id: int):
    deadline = time.monotonic() + max(0, int(game.last_word_seconds))
    try:
        await run_reliable_timer(
            bot, game, message_id, deadline,
            lambda remaining: (
                f"🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n"
                f"<b>{safe_name(game, player_id)}</b> может написать последнее сообщение.\n\n"
                f"⏱ <b>{remaining:02d} сек.</b>"
            ),
        )
    finally:
        game.active_last_words.discard(player_id)
        game.last_word_used.add(player_id)
        if game.last_word_player == player_id:
            game.last_word_player = None



async def run_countdown_message(bot: Bot, game: Game, message, title: str, seconds: int, footer: str = ""):
    deadline = time.monotonic() + max(0, int(seconds))
    await run_reliable_timer(
        bot, game, message.message_id, deadline,
        lambda remaining: (
            f"{title}\n\n⏱ <b>{remaining // 60:02d}:{remaining % 60:02d}</b>"
            + (f"\n\n{footer}" if footer else "")
        ),
    )


async def run_day(bot: Bot, game: Game):
    game.day_number += 1; game.phase = "day_discussion"
    await update_main_game_message(bot, game)
    try:
        message = await send_game_message(bot, game, "☀️ <b>ДЕНЬ</b>\n\n💬 <b>ОБСУЖДЕНИЕ</b>", parse_mode="HTML")
    except Exception as error:
        print(f"⚠️ Не удалось открыть обсуждение: {error}")
        # One retry prevents a transient Telegram API failure from killing the game.
        await asyncio.sleep(1)
        message = await send_game_message(bot, game, "☀️ <b>ДЕНЬ</b>\n\n💬 <b>ОБСУЖДЕНИЕ</b>", parse_mode="HTML")
    await run_countdown_message(bot, game, message, "☀️ <b>ДЕНЬ</b>\n\n💬 <b>ОБСУЖДЕНИЕ</b>", game.discussion_seconds)
    if not game.started: return
    await conduct_vote(bot, game, None)


def get_vote_live_text(game: Game, candidates: list[int]) -> str:
    lines = ["🗳 <b>ГОЛОСОВАНИЕ</b>", ""]
    if game.day_votes:
        lines.append("<b>КТО ЗА КОГО ПРОГОЛОСОВАЛ:</b>")
        for voter, target in game.day_votes.items():
            lines.append(f"{safe_name(game, voter)} → {safe_name(game, target)}")
    else:
        lines.append("Пока никто не проголосовал.")
    lines.append("")
    lines.append("<b>СЧЁТ:</b>")
    counts = Counter(game.day_votes.values())
    for uid in candidates:
        lines.append(f"{safe_name(game, uid)} — <b>{counts.get(uid, 0)}</b>")
    return "\n".join(lines)


async def send_private_vote_prompts(bot: Bot, game: Game, candidates: list[int]):
    # В личном чате список кандидатов персональный: своё имя здесь не показывается.
    for voter in game.alive_players():
        choices = [(uid, game.player_names.get(uid, "Игрок")) for uid in candidates if uid in game.alive and uid != voter]
        if not choices:
            continue
        try:
            await send_private_game_message(
                bot, game, voter,
                f"🗳 <b>ГОЛОСОВАНИЕ</b>\n\nВыберите игрока, затем нажмите «ПОДТВЕРДИТЬ ГОЛОС».\n\n⏱ <b>{game.vote_seconds} секунд</b>",
                reply_markup=vote_keyboard(game.chat_id, choices), parse_mode="HTML")
        except Exception:
            pass


async def conduct_vote(bot: Bot, game: Game, candidates: list[int] | None):
    game.phase = "day_vote"
    game.reset_day_votes()
    alive = game.alive_players()
    if candidates is None:
        game.tie_candidates.clear()
        ids = alive
    else:
        game.tie_candidates = [uid for uid in candidates if uid in alive]
        ids = game.tie_candidates

    players = [(uid, game.player_names.get(uid, "Игрок")) for uid in ids if uid in game.alive]
    await update_main_game_message(bot, game)
    vote_message = await send_game_message(
        bot,
        game,
        get_vote_live_text(game, ids),
        reply_markup=vote_keyboard(game.chat_id, players),
        parse_mode="HTML",
    )
    game.vote_message_id = vote_message.message_id

    # ВАЖНО: дедлайн и таймер запускаются ДО отправки личных сообщений.
    # Если Telegram задержит одно из ЛС, видимый таймер всё равно продолжит тикать.
    deadline = time.monotonic() + max(0, int(game.vote_seconds))
    game.vote_deadline = deadline
    timer_task = asyncio.create_task(
        run_reliable_timer(
            bot,
            game,
            game.vote_message_id,
            deadline,
            lambda remaining: get_vote_live_text(game, ids) + f"\n\n⏱ <b>{remaining // 60:02d}:{remaining % 60:02d}</b>",
            lambda _remaining: vote_keyboard(game.chat_id, players),
        )
    )
    timer_task.add_done_callback(_log_background_task_error)

    try:
        await send_private_vote_prompts(bot, game, ids)

        remaining = max(0.0, deadline - time.monotonic())
        if remaining > 0 and not game.all_day_votes_complete():
            try:
                await asyncio.wait_for(game.action_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass

        alive = game.alive_players()
        for voter in alive:
            if voter not in game.day_votes:
                selected = game.day_vote_selection.get(voter)
                choices = [uid for uid in ids if uid in game.alive and uid != voter]
                if selected in choices:
                    game.day_votes[voter] = selected
                elif choices:
                    game.day_votes[voter] = random.choice(choices)

        await publish_vote_results(bot, game)
        counts = Counter(game.day_votes.values())
        if not counts:
            return
        high = max(counts.values())
        leaders = [uid for uid, c in counts.items() if c == high]
        if len(leaders) > 1 and candidates is None:
            game.tie_candidates = leaders
            await send_game_message(
                bot, game,
                "⚖️ <b>НИЧЬЯ</b>\n\nРешающее голосование между лидерами.",
                reply_markup=bot_chat_keyboard(), parse_mode="HTML",
            )
            await conduct_vote(bot, game, leaders)
            return
        if len(leaders) > 1:
            await send_game_message(
                bot, game,
                "⚖️ <b>СНОВА НИЧЬЯ</b>\n\nНикто не изгнан.",
                parse_mode="HTML",
            )
            game.tie_candidates.clear()
            return

        eliminated = leaders[0]
        game.tie_candidates.clear()
        if eliminated in game.alive:
            game.alive.remove(eliminated)
        await send_game_message(
            bot, game,
            f"🔴 <b>{safe_name(game, eliminated)}</b> покидает игру.\n\n🔴 Последнее слово.",
            parse_mode="HTML",
        )
        await run_last_word(bot, game, eliminated)
    finally:
        if not timer_task.done():
            timer_task.cancel()
            try:
                await timer_task
            except asyncio.CancelledError:
                pass


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
    # Сообщение хода оставляем в чате бота, чтобы игрок видел историю действия.
    await callback.answer()
    try:
        if role == MAFIA:
            await send_game_message(bot, game, "🔫 <b>МАФИЯ ВЫБИРАЕТ ЖЕРТВУ.</b>", reply_markup=bot_chat_keyboard(), parse_mode="HTML")
        else:
            await send_game_message(bot, game, "💊 <b>ДОКТОР ВЫБИРАЕТ, КОГО СПАСТИ.</b>", reply_markup=bot_chat_keyboard(), parse_mode="HTML")
    except Exception as error:
        print(f"⚠️ Не удалось показать подтверждение ночного хода: {type(error).__name__}: {error}")
    if game.night_actions_complete():
        game.action_event.set()


@dp.callback_query(F.data.startswith("night_commissioner:"))
async def open_commissioner_action(callback: CallbackQuery, bot: Bot):
    await commissioner_menu(callback, bot)


async def commissioner_menu(callback: CallbackQuery, bot: Bot):
    try: chat_id = int(callback.data.split(":")[1])
    except Exception: await callback.answer("❌ Некорректная игра.", show_alert=True); return
    game = games.get(chat_id); uid = callback.from_user.id
    if not game or game.phase != "night" or uid not in game.alive or game.roles.get(uid) != COMMISSIONER:
        await callback.answer("❌ Действие недоступно.", show_alert=True); return
    targets = [(tid, game.player_names.get(tid, "Игрок")) for tid in game.alive if tid != uid]
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
    await send_private_game_message(bot, game, uid, "☠️ <b>КОГО УБИТЬ?</b>", reply_markup=target_keyboard(chat_id, targets, "commissioner_kill_target", game.commissioner_kill_target), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("commissioner_target:"))
async def commissioner_target_handler(callback: CallbackQuery, bot: Bot):
    try: _, chat_s, target_s = callback.data.split(":"); chat_id=int(chat_s); target_id=int(target_s)
    except Exception: await callback.answer("❌ Некорректная кнопка.", show_alert=True); return
    game=games.get(chat_id); uid=callback.from_user.id
    if not game or game.phase!="night" or uid not in game.alive or game.roles.get(uid)!=COMMISSIONER or target_id not in game.alive or target_id==uid:
        await callback.answer("❌ Действие недоступно.", show_alert=True); return
    if game.commissioner_kill_target is not None:
        await callback.answer("❌ Вы уже выбрали убийство. Комиссар может сделать только одно действие за ночь.", show_alert=True); return
    game.commissioner_target=target_id
    result={MAFIA:"🔴 МАФИЯ", DOCTOR:"💊 ДОКТОР", COMMISSIONER:"🔎 КОМИССАР"}.get(game.roles.get(target_id),"🟢 МИРНЫЙ ЖИТЕЛЬ")
    if game.night_actions_complete():
        game.action_event.set()
    try:
        await send_game_message(bot, game, "🔎 <b>КОМИССАР ВЫШЕЛ НА ПОИСКИ МАФИИ.</b>", reply_markup=bot_chat_keyboard(), parse_mode="HTML")
    except Exception as error:
        print(f"⚠️ Не удалось показать подтверждение хода комиссара: {type(error).__name__}: {error}")
    await callback.answer()



@dp.callback_query(F.data.startswith("commissioner_kill_target:"))
async def commissioner_kill_handler(callback: CallbackQuery, bot: Bot):
    try: _, chat_s, target_s=callback.data.split(":"); chat_id=int(chat_s); target_id=int(target_s)
    except Exception: await callback.answer("❌ Некорректная кнопка.", show_alert=True); return
    game=games.get(chat_id); uid=callback.from_user.id
    if not game or game.phase!="night" or uid not in game.alive or game.roles.get(uid)!=COMMISSIONER or target_id not in game.alive or target_id==uid:
        await callback.answer("❌ Действие недоступно.", show_alert=True); return
    if game.commissioner_target is not None:
        await callback.answer("❌ Вы уже выбрали проверку. Комиссар может сделать только одно действие за ночь.", show_alert=True); return
    game.commissioner_kill_target=target_id
    if game.night_actions_complete():
        game.action_event.set()
    try:
        await send_game_message(bot, game, "☠️ <b>КОМИССАР ХОЧЕТ УБИТЬ.</b>", reply_markup=bot_chat_keyboard(), parse_mode="HTML")
    except Exception as error:
        print(f"⚠️ Не удалось показать подтверждение убийства комиссара: {type(error).__name__}: {error}")
    await callback.answer()


@dp.callback_query(F.data.startswith("cancel_action:"))
async def cancel_action_handler(callback: CallbackQuery):
    try: _, chat_s, prefix=callback.data.split(":",2); chat_id=int(chat_s)
    except Exception: await callback.answer("❌ Некорректная кнопка.", show_alert=True); return
    game=games.get(chat_id); uid=callback.from_user.id
    if not game or game.phase!="night": return
    if prefix=="mafia_target": game.mafia_votes.pop(uid,None)
    elif prefix=="doctor_target": game.doctor_target=None
    elif prefix=="commissioner_target": game.commissioner_target=None
    elif prefix=="commissioner_kill_target": game.commissioner_kill_target=None
    await callback.answer("❌ Выбор отменён.")


@dp.callback_query(F.data.startswith("vote:"))
async def vote_handler(callback: CallbackQuery):
    try: _, chat_s, target_s = callback.data.split(":"); chat_id = int(chat_s); target_id = int(target_s)
    except Exception:
        await callback.answer("❌ Некорректное голосование.", show_alert=True); return
    game = games.get(chat_id); voter = callback.from_user.id
    if not game or game.phase != "day_vote" or voter not in game.alive or target_id not in game.alive:
        await callback.answer("❌ Голосование недоступно.", show_alert=True); return
    if target_id == voter:
        await callback.answer("❌ За себя голосовать нельзя.", show_alert=True); return
    game.day_vote_selection[voter] = target_id
    await callback.answer()


@dp.callback_query(F.data.startswith("vote_confirm:"))
async def vote_confirm_handler(callback: CallbackQuery, bot: Bot):
    try: chat_id = int(callback.data.split(":")[1])
    except Exception:
        await callback.answer("❌ Некорректное голосование.", show_alert=True); return
    game = games.get(chat_id); voter = callback.from_user.id
    if not game or game.phase != "day_vote" or voter not in game.alive:
        await callback.answer("❌ Голосование недоступно.", show_alert=True); return
    target_id = game.day_vote_selection.get(voter)
    if target_id is None:
        await callback.answer("Сначала выберите игрока.", show_alert=True); return
    if target_id == voter or target_id not in game.alive:
        await callback.answer("❌ Этот выбор недоступен.", show_alert=True); return
    game.day_votes[voter] = target_id
    candidates = [uid for uid in (game.tie_candidates or game.alive_players()) if uid in game.alive]
    if game.vote_message_id:
        try:
            players = [(uid, game.player_names.get(uid, "Игрок")) for uid in candidates]
            remaining = max(0, int(getattr(game, "vote_deadline", time.monotonic()) - time.monotonic() + 0.999))
            await bot.edit_message_text(
                chat_id=chat_id, message_id=game.vote_message_id,
                text=get_vote_live_text(game, candidates) + f"\n\n⏱ <b>00:{remaining:02d}</b>",
                reply_markup=vote_keyboard(chat_id, players), parse_mode="HTML")
        except Exception:
            pass
    await callback.answer(f"✅ Голос подтверждён: {game.player_names.get(target_id, 'Игрок')}")
    if game.all_day_votes_complete(): game.action_event.set()


async def finish_game(bot: Bot, game: Game, winner: str):
    game.started=False; game.phase="finished"; game.action_event.set()
    await update_main_game_message(bot, game)
    title="🔫 <b>МАФИЯ ПОБЕДИЛА!</b>" if winner=="mafia" else "🏆 <b>ГОРОД ПОБЕДИЛ!</b>"
    await send_game_message(bot, game, f"🏆 <b>ИГРА ОКОНЧЕНА</b>\n\n{title}\n\nСыграем ещё раз?", reply_markup=postgame_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "postgame_new")
async def postgame_new_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id=callback.message.chat.id
    if not await is_group_admin(bot,chat_id,callback.from_user.id):
        await callback.answer("⚠️ Только администратор.",show_alert=True); return
    old=games.get(chat_id)
    if not old: return
    task=game_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    await cleanup_game_messages(bot,chat_id)
    old.reset_to_lobby(False)
    game_tasks.pop(chat_id, None)
    games[chat_id]=old
    lobby = await bot.send_message(chat_id,get_lobby_text(old),reply_markup=lobby_keyboard(True,False),parse_mode="HTML")
    game_messages[chat_id]=lobby.message_id; game_message_ids.setdefault(chat_id,set()).add(lobby.message_id)
    await callback.answer("🎮 Новое лобби готово.")


@dp.callback_query(F.data == "postgame_end")
async def postgame_end_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message: return
    chat_id=callback.message.chat.id
    if not await is_group_admin(bot,chat_id,callback.from_user.id):
        await callback.answer("⚠️ Только администратор.",show_alert=True); return
    game=games.get(chat_id)
    if game: game.reset_to_lobby(False)
    await cleanup_game_messages(bot,chat_id)
    welcome=await bot.send_message(chat_id,get_welcome_text(callback.message.chat.title or "MAFIA"),reply_markup=admin_start_keyboard(),parse_mode="HTML")
    game_messages[chat_id]=welcome.message_id
    game_message_ids.setdefault(chat_id,set()).add(welcome.message_id)
    await callback.answer("🏁 Игра завершена.")


@dp.callback_query(F.data == "admin_stop")
async def admin_stop_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message:return
    chat_id=callback.message.chat.id
    if not await is_group_admin(bot,chat_id,callback.from_user.id): await callback.answer("⚠️ Только администратор.",show_alert=True); return
    game=games.get(chat_id)
    if game: game.stop()
    task=game_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    await cleanup_game_messages(bot,chat_id)
    welcome=await bot.send_message(chat_id,get_welcome_text(callback.message.chat.title or "MAFIA"),reply_markup=admin_start_keyboard(),parse_mode="HTML")
    game_messages[chat_id]=welcome.message_id; game_message_ids.setdefault(chat_id,set()).add(welcome.message_id)
    await callback.answer("⏹ Игра остановлена.")


@dp.callback_query(F.data == "admin_restart")
async def admin_restart_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message:return
    chat_id=callback.message.chat.id
    if not await is_group_admin(bot,chat_id,callback.from_user.id): await callback.answer("⚠️ Только администратор.",show_alert=True); return
    game=games.get(chat_id)
    if not game or not game.can_start(): await callback.answer("❌ Недостаточно игроков.",show_alert=True); return
    task=game_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    await cleanup_game_messages(bot,chat_id)
    game.restart()
    restart_message = await bot.send_message(chat_id,
        f"🔄 <b>ИГРА ПЕРЕЗАПУСКАЕТСЯ</b>\n\n👥 Игроков: <b>{len(game.players)}</b>\n\n🎲 Роли будут распределены заново.",
        reply_markup=admin_game_keyboard(chat_id,BOT_USERNAME), parse_mode="HTML")
    game_messages[chat_id]=restart_message.message_id
    game_message_ids.setdefault(chat_id,set()).add(restart_message.message_id)
    await callback.answer("🔄 Игра перезапускается.")
    await start_game_task(bot,game)


@dp.callback_query(F.data == "admin_new_game")
async def admin_new_game_handler(callback: CallbackQuery, bot: Bot):
    if not callback.message:return
    chat_id=callback.message.chat.id
    if not await is_group_admin(bot,chat_id,callback.from_user.id): await callback.answer("⚠️ Только администратор.",show_alert=True); return
    task=game_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    await cleanup_game_messages(bot,chat_id)
    game=Game(chat_id=chat_id,creator_id=callback.from_user.id,group_title=callback.message.chat.title or "MAFIA")
    games[chat_id]=game
    await callback.message.edit_text(get_lobby_text(game),reply_markup=lobby_keyboard(True,False),parse_mode="HTML")
    game_messages[chat_id]=callback.message.message_id; game_message_ids.setdefault(chat_id,set()).add(callback.message.message_id)
    await callback.answer("🆕 Новое лобби создано.")


@dp.callback_query(F.data.startswith("my_night:"))
async def my_night_handler(callback: CallbackQuery, bot: Bot):
    try: chat_id=int(callback.data.split(":")[1])
    except Exception: await callback.answer("❌ Некорректная игра.",show_alert=True); return
    game=games.get(chat_id)
    if not game or not game.started or game.phase!="night": await callback.answer("🌅 Сейчас нет ночного хода.",show_alert=True); return
    await callback.answer("🔐 Откройте личный чат с ботом через кнопку из игрового сообщения.",show_alert=True)


async def update_main_game_message(bot: Bot, game: Game):
    message_id=game_messages.get(game.chat_id)
    if not message_id:return
    try:
        await bot.edit_message_text(chat_id=game.chat_id,message_id=message_id,text=get_game_status_text(game) if game.started else get_welcome_text(game.group_title),reply_markup=admin_game_keyboard(game.chat_id,BOT_USERNAME) if game.started else admin_start_keyboard(),parse_mode="HTML")
    except Exception: pass


@dp.message()
async def group_message_handler(message: Message, bot: Bot):
    if message.chat.type==ChatType.PRIVATE:return
    game=games.get(message.chat.id)
    if not game:return
    uid=message.from_user.id if message.from_user else None
    if uid is None:return
    if game.started and uid in game.active_last_words and message.text:
        text=message.text
        await delete_message_safe(bot,message.chat.id,message.message_id)
        try:
            await send_game_message(
                bot, game,
                f"🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n👤 <b>{safe_name(game,uid)}</b>\n\n«{html.escape(text)}»",
                parse_mode="HTML"
            )
        except Exception as error:
            print(f"⚠️ Не удалось показать последнее слово: {type(error).__name__}: {error}")
        game.last_word_text = text
        game.active_last_words.discard(uid)
        game.last_word_used.add(uid)
        if game.last_word_player == uid:
            game.last_word_player = None
        return
    if game.started and uid in game.players and uid not in game.alive:
        await delete_message_safe(bot,message.chat.id,message.message_id)


async def main():
    global BOT_USERNAME
    bot=Bot(token=BOT_TOKEN)
    me=await bot.get_me(); BOT_USERNAME=me.username
    await bot.set_my_commands([
        BotCommand(command="mafia", description="Создать новую игру"),
        BotCommand(command="stop", description="Остановить игру"),
        BotCommand(command="restart", description="Перезапустить игру"),
    ], scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands([
        BotCommand(command="start", description="Активировать личный режим"),
        BotCommand(command="reset", description="Сбросить личный режим"),
        BotCommand(command="stop", description="Остановить личный режим"),
    ], scope=BotCommandScopeAllPrivateChats())
    print("🟢 Mafia Bot запускается...")
    await setup_bot_avatar(bot)
    print("🟢 Mafia Bot запущен")
    try: await dp.start_polling(bot)
    finally: await bot.session.close()


if __name__=="__main__": asyncio.run(main())

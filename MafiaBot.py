import asyncio, os, html, random
from collections import Counter
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatType, ChatMemberStatus
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from game import Game, MAFIA, DOCTOR, COMMISSIONER, CIVILIAN, ROLE_NAMES, ROLE_DESCRIPTIONS
BOT_TOKEN=os.getenv("BOT_TOKEN")
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN не найден")
dp=Dispatcher(); games={}; tasks={}; main_messages={}; private_messages={}; BOT_USERNAME=None
bot_settings={}; BOT_BASE=-900000000000; BOT_NAMES=["Алексей","Макс","Виктор","Данияр","Илья","Рустам","Сергей","Тимур","Марат"]
DIFF={"easy":"🟢 Легко","medium":"🟡 Средне","hard":"🔴 Сложно"}
async def _is_admin(bot,cid,uid):
    try:return (await bot.get_chat_member(cid,uid)).status in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.CREATOR)
    except:return False
def is_bot(uid): return uid<=BOT_BASE
def bots(game): return [u for u in game.players if is_bot(u)]
def humans(game): return [u for u in game.players if not is_bot(u)]
def bot_count(game): return len(bots(game))
def diff(game): return bot_settings.get(game.chat_id,{}).get("difficulty","medium")
def bot_id(game,i): return BOT_BASE-(abs(game.chat_id)%1000000)*100-i
def esc(game,u): return html.escape(game.player_names.get(u,"Игрок"))
def choose(game,candidates,role):
    c=[u for u in candidates if u in game.alive]
    if not c:return None
    d=diff(game)
    if d=="easy":return random.choice(c)
    score={u:random.random()*2 for u in c}
    for _,t in game.day_votes.items():
        if t in score:score[t]+=3 if d=="hard" else 1.5
    return max(c,key=score.get)
def bot_kb(game):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="1",callback_data="bc:1"),InlineKeyboardButton(text="3",callback_data="bc:3"),InlineKeyboardButton(text="6",callback_data="bc:6"),InlineKeyboardButton(text="9",callback_data="bc:9")],[InlineKeyboardButton(text="🟢 Легко",callback_data="bd:easy"),InlineKeyboardButton(text="🟡 Средне",callback_data="bd:medium"),InlineKeyboardButton(text="🔴 Сложно",callback_data="bd:hard")],[InlineKeyboardButton(text="❌ ОТКЛЮЧИТЬ БОТОВ",callback_data="boff")],[InlineKeyboardButton(text="⬅️ НАЗАД",callback_data="bback")]])
def bot_text(g):return f"🤖 <b>БОТЫ</b>\n\nКоличество: <b>{bot_count(g)}</b>\nСложность: <b>{DIFF[diff(g)]}</b>\n\nВыберите количество и уровень. Максимальный состав — 12 участников."
def lobby_k(can):
    a=[[InlineKeyboardButton(text="🎮 Я ИГРАЮ",callback_data="join")],[InlineKeyboardButton(text="🤖 БОТЫ",callback_data="bots")]]
    if can:a.append([InlineKeyboardButton(text="▶️ НАЧАТЬ ИГРУ",callback_data="start")])
    a.append([InlineKeyboardButton(text="⚙️ НАСТРОЙКИ",callback_data="settings")]);return InlineKeyboardMarkup(inline_keyboard=a)
def game_k(cid):
    a=[[InlineKeyboardButton(text="🎭 МОЯ РОЛЬ",callback_data=f"role:{cid}")]]
    if BOT_USERNAME:a.append([InlineKeyboardButton(text="🎭 МОЙ НОЧНОЙ ХОД",url=f"https://t.me/{BOT_USERNAME}?start=game_{cid}")])
    return InlineKeyboardMarkup(inline_keyboard=a)
def target_k(cid,items,prefix):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🎯 {n[:28]}",callback_data=f"{prefix}:{cid}:{u}")] for u,n in items])
def vote_k(cid,items):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🗳 {n[:28]}",callback_data=f"vote:{cid}:{u}")] for u,n in items])
async def gm(bot,g,text,**kw):
    m=await bot.send_message(g.chat_id,text,**kw);main_messages[g.chat_id]=m.message_id;return m
async def pm(bot,g,uid,text,**kw):
    m=await bot.send_message(uid,text,**kw);private_messages.setdefault(g.chat_id,set()).add((uid,m.message_id));return m
async def cleanup(bot,cid):
    mid=main_messages.pop(cid,None)
    if mid:
        try:await bot.delete_message(cid,mid)
        except:pass
    for uid,mid in private_messages.pop(cid,set()):
        try:await bot.delete_message(uid,mid)
        except:pass
def lobby_text(g):
    lines=[f"🎭 <b>MAFIA — {html.escape(g.group_title)}</b>",f"👥 Игроков: <b>{len(g.players)}</b>",f"🤖 Ботов: <b>{bot_count(g)}</b>",""]
    lines += [f"{i}. {'🔴' if g.started and u not in g.alive else '🟢'} {esc(g,u)}" for i,u in enumerate(g.players,1)]
    lines += ["",("⏳ Нужно ещё <b>%d игрока</b>"%(g.MIN_PLAYERS-len(g.players)) if len(g.players)<g.MIN_PLAYERS else "✅ <b>Минимум игроков набран!</b>")]
    return "\n".join(lines)
def add_bots(g,n):
    for u in bots(g):g.players.remove(u);g.player_names.pop(u,None)
    for i in range(1,n+1):u=bot_id(g,i);g.players.append(u);g.player_names[u]=f"🤖 {BOT_NAMES[i-1]}"
@dp.message(CommandStart())
async def start_cmd(m:Message):
    global BOT_USERNAME;BOT_USERNAME=(await m.bot.get_me()).username
    if m.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP):return
    arg=(m.text or "").split(maxsplit=1)[1] if " " in (m.text or "") else ""
    if arg.startswith("game_"):
        try:g=games.get(int(arg[5:]))
        except:g=None
        if g and g.started and m.from_user.id in g.alive and g.phase=="night":await private_action(m.bot,g,m.from_user.id);return
    await m.answer("🎭 <b>MAFIA</b>\n\nЛичный режим активирован.\nСекретные действия видны только вам.",parse_mode="HTML")
@dp.message(Command("reset"))
async def reset_cmd(m:Message):
    if m.chat.type==ChatType.PRIVATE:await m.answer("♻️ Личный режим сброшен. Используйте /start.")
@dp.message(Command("stop"))
async def private_stop(m:Message):
    if m.chat.type==ChatType.PRIVATE:await m.answer("⏹ Личный режим остановлен. Используйте /start.")
@dp.my_chat_member()
async def added(e:ChatMemberUpdated,bot:Bot):
    if e.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP):return
    if e.new_chat_member.status not in (ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR):return
    await bot.send_message(e.chat.id,"🎭 <b>MAFIA</b>\n\nГотовы сыграть? Минимум 4 игрока.\n🔒 Секретные действия — приватно.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎭 НАЧАТЬ МАФИЮ",callback_data="new")]]),parse_mode="HTML")
@dp.message(Command("mafia"))
async def mafia_cmd(m:Message,bot:Bot):
    if m.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP) or not await _is_admin(bot,m.chat.id,m.from_user.id):return
    cid=m.chat.id;g=games.get(cid)
    if g and (g.started or g.players):await m.answer("⚠️ Уже есть лобби или игра.");return
    g=Game(cid,m.from_user.id,m.chat.title or "MAFIA");games[cid]=g;bot_settings[cid]={"count":0,"difficulty":"medium"};await gm(bot,g,lobby_text(g),reply_markup=lobby_k(False),parse_mode="HTML")
@dp.callback_query(F.data=="new")
async def new_game(c:CallbackQuery,bot:Bot):
    if not c.message or not await _is_admin(bot,c.message.chat.id,c.from_user.id):await c.answer("⚠️ Только администратор.",show_alert=True);return
    cid=c.message.chat.id;g=Game(cid,c.from_user.id,c.message.chat.title or "MAFIA");games[cid]=g;bot_settings[cid]={"count":0,"difficulty":"medium"};await c.message.edit_text(lobby_text(g),reply_markup=lobby_k(False),parse_mode="HTML");await c.answer()
@dp.callback_query(F.data=="join")
async def join(c:CallbackQuery):
    g=games.get(c.message.chat.id) if c.message else None
    if not g or g.started:await c.answer("❌ Сейчас нельзя присоединиться.",show_alert=True);return
    u=c.from_user.id
    if u in g.players:await c.answer("Вы уже играете.",show_alert=True);return
    g.add_player(u);g.player_names[u]=c.from_user.full_name or "Игрок";await c.message.edit_text(lobby_text(g),reply_markup=lobby_k(g.can_start()),parse_mode="HTML");await c.answer("🎮 Вы в игре!")
@dp.callback_query(F.data=="bots")
async def bots_menu(c:CallbackQuery,bot:Bot):
    if not c.message or not await _is_admin(bot,c.message.chat.id,c.from_user.id):await c.answer("⚠️ Только администратор.",show_alert=True);return
    g=games.get(c.message.chat.id)
    if not g or g.started:await c.answer("❌ Только в лобби.",show_alert=True);return
    await c.message.edit_text(bot_text(g),reply_markup=bot_kb(g),parse_mode="HTML");await c.answer()
@dp.callback_query(F.data.startswith("bc:"))
async def bots_count(c:CallbackQuery,bot:Bot):
    if not c.message or not await _is_admin(bot,c.message.chat.id,c.from_user.id):return
    g=games.get(c.message.chat.id);n=int(c.data[3:]);maxn=12-len(humans(g))
    if not g or g.started or n>maxn:await c.answer(f"❌ Максимум сейчас: {max(0,maxn)}",show_alert=True);return
    add_bots(g,n);bot_settings.setdefault(g.chat_id,{})["count"]=n;await c.message.edit_text(bot_text(g),reply_markup=bot_kb(g),parse_mode="HTML");await c.answer()
@dp.callback_query(F.data.startswith("bd:"))
async def bots_diff(c:CallbackQuery,bot:Bot):
    if not c.message or not await _is_admin(bot,c.message.chat.id,c.from_user.id):return
    g=games.get(c.message.chat.id);d=c.data[3:]
    if not g or g.started or d not in DIFF:return
    bot_settings.setdefault(g.chat_id,{"count":bot_count(g)})["difficulty"]=d;await c.message.edit_text(bot_text(g),reply_markup=bot_kb(g),parse_mode="HTML");await c.answer(DIFF[d])
@dp.callback_query(F.data=="boff")
async def bots_off(c:CallbackQuery,bot:Bot):
    if not c.message or not await _is_admin(bot,c.message.chat.id,c.from_user.id):return
    g=games.get(c.message.chat.id)
    if not g or g.started:return
    add_bots(g,0);bot_settings.setdefault(g.chat_id,{})["count"]=0;await c.message.edit_text(lobby_text(g),reply_markup=lobby_k(g.can_start()),parse_mode="HTML");await c.answer("❌ Боты отключены")
@dp.callback_query(F.data=="bback")
async def bots_back(c:CallbackQuery):
    g=games.get(c.message.chat.id) if c.message else None
    if g:await c.message.edit_text(lobby_text(g),reply_markup=lobby_k(g.can_start()),parse_mode="HTML")
    await c.answer()
@dp.message(Command("bots"))
async def bots_cmd(m:Message,bot:Bot):
    if m.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP) and await _is_admin(bot,m.chat.id,m.from_user.id):
        g=games.get(m.chat.id)
        if g and not g.started:await m.answer(bot_text(g),reply_markup=bot_kb(g),parse_mode="HTML")
@dp.message(Command("bots_off"))
async def bots_off_cmd(m:Message,bot:Bot):
    if m.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP) and await _is_admin(bot,m.chat.id,m.from_user.id):
        g=games.get(m.chat.id)
        if g and not g.started:add_bots(g,0);bot_settings.setdefault(g.chat_id,{})["count"]=0;await m.answer("❌ Боты отключены.")
@dp.callback_query(F.data=="start")
async def start_game(c:CallbackQuery,bot:Bot):
    if not c.message or not await _is_admin(bot,c.message.chat.id,c.from_user.id):await c.answer("⚠️ Только администратор.",show_alert=True);return
    g=games.get(c.message.chat.id)
    if not g or not g.can_start():await c.answer("❌ Нужно минимум 4 игрока.",show_alert=True);return
    if len(g.players)>12:await c.answer("❌ Максимум 12 участников.",show_alert=True);return
    g.start();await c.message.edit_text(f"🎭 <b>ИГРА НАЧИНАЕТСЯ</b>\n\n👥 {len(g.players)} участников\n🔒 Роли приватны.",reply_markup=game_k(g.chat_id),parse_mode="HTML");tasks[g.chat_id]=asyncio.create_task(run_game(bot,g));await c.answer("🎭 Игра началась!")
async def private_action(bot,g,uid):
    r=g.roles.get(uid)
    if r==MAFIA:
        t=[(u,g.player_names.get(u,"Игрок")) for u in g.alive if g.roles.get(u)!=MAFIA];await pm(bot,g,uid,"🔫 <b>ХОД МАФИИ</b>\n\nВыберите жертву.",reply_markup=target_k(g.chat_id,t,"mt"),parse_mode="HTML")
    elif r==DOCTOR:
        t=[(u,g.player_names.get(u,"Игрок")) for u in g.doctor_targets()];await pm(bot,g,uid,"💊 <b>ХОД ДОКТОРА</b>\n\nКого спасти?",reply_markup=target_k(g.chat_id,t,"dt"),parse_mode="HTML")
    elif r==COMMISSIONER:await pm(bot,g,uid,"🔎 <b>ХОД КОМИССАРА</b>\n\nВыберите проверку.",reply_markup=target_k(g.chat_id,[(u,g.player_names.get(u,"Игрок")) for u in g.alive if u!=uid],"ct"),parse_mode="HTML")
@dp.callback_query(F.data.startswith("mt:"))
async def mt(c:CallbackQuery):await night_target(c,MAFIA)
@dp.callback_query(F.data.startswith("dt:"))
async def dt(c:CallbackQuery):await night_target(c,DOCTOR)
async def night_target(c,r):
    try:_,cid,tid=c.data.split(":");cid=int(cid);tid=int(tid)
    except:await c.answer("❌ Ошибка.");return
    g=games.get(cid);u=c.from_user.id
    if not g or g.phase!="night" or g.roles.get(u)!=r or tid not in g.alive:await c.answer("❌ Действие недоступно.",show_alert=True);return
    if r==MAFIA:g.mafia_votes[u]=tid
    else:g.doctor_target=tid
    try:await c.message.delete()
    except:pass
    if (r==MAFIA and len(g.mafia_votes)>=len(g.alive_mafia())) or r==DOCTOR:g.action_event.set()
    await c.answer("✅ Ход принят")
@dp.callback_query(F.data.startswith("ct:"))
async def ct(c:CallbackQuery):
    try:_,cid,tid=c.data.split(":");cid=int(cid);tid=int(tid)
    except:return
    g=games.get(cid);u=c.from_user.id
    if not g or g.phase!="night" or g.roles.get(u)!=COMMISSIONER or tid not in g.alive:return
    g.commissioner_target=tid;role=ROLE_NAMES.get(g.roles.get(tid),"роль не определена");await c.answer(role,show_alert=True);g.action_event.set()
@dp.callback_query(F.data.startswith("vote:"))
async def vote(c:CallbackQuery):
    try:_,cid,tid=c.data.split(":");cid=int(cid);tid=int(tid)
    except:return
    g=games.get(cid);u=c.from_user.id
    if not g or g.phase!="day_vote" or u not in g.alive or tid not in g.alive or tid==u:return
    g.day_votes[u]=tid
    if g.all_day_votes_complete():g.action_event.set()
    await c.answer("🗳 Голос принят")
async def run_game(bot,g):
    try:
        g.assign_roles()
        for u in g.players:
            if not is_bot(u):await pm(bot,g,u,f"🎭 <b>ВАША РОЛЬ</b>\n\n{ROLE_DESCRIPTIONS[g.roles[u]]}",parse_mode="HTML")
        while g.started:
            if g.winner():break
            await night(bot,g)
            if g.winner() or not g.started:break
            await day(bot,g)
        if g.started:await finish(bot,g,g.winner())
    except asyncio.CancelledError:raise
    except Exception as e:print("GAME ERROR",g.chat_id,e)
async def night(bot,g):
    g.phase="night";g.night_number+=1;g.reset_night_actions();await gm(bot,g,"🌙 <b>ГОРОД ЗАСЫПАЕТ</b>\n\nНочные действия выполняются тайно.",parse_mode="HTML")
    mafia=g.alive_mafia()
    for u in mafia:
        if is_bot(u):
            t=choose(g,[x for x in g.alive if g.roles.get(x)!=MAFIA],MAFIA)
            if t is not None:g.mafia_votes[u]=t
        else:await private_action(bot,g,u)
    g.action_event.clear()
    if len(g.mafia_votes)>=len(g.alive_mafia()):g.action_event.set()
    try:await asyncio.wait_for(g.action_event.wait(),g.night_seconds)
    except asyncio.TimeoutError:pass
    for u in mafia:
        if u not in g.mafia_votes:
            t=[x for x in g.alive if g.roles.get(x)!=MAFIA]
            if t:g.mafia_votes[u]=random.choice(t)
    doc=next((u for u in g.alive if g.roles.get(u)==DOCTOR),None)
    if doc:
        if is_bot(doc):g.doctor_target=choose(g,list(g.doctor_targets()),DOCTOR)
        else:
            await private_action(bot,g,doc);g.action_event.clear()
            try:await asyncio.wait_for(g.action_event.wait(),g.night_seconds)
            except asyncio.TimeoutError:pass
            if g.doctor_target is None:g.doctor_target=random.choice(g.doctor_targets()) if g.doctor_targets() else None
    com=next((u for u in g.alive if g.roles.get(u)==COMMISSIONER),None)
    if com:
        if is_bot(com):
            t=[u for u in g.alive if u!=com];g.commissioner_target=choose(g,t,COMMISSIONER)
            if diff(g)=="hard" and t and random.random()<.35:g.commissioner_kill_target=choose(g,t,COMMISSIONER)
        else:
            await private_action(bot,g,com);g.action_event.clear()
            try:await asyncio.wait_for(g.action_event.wait(),g.night_seconds)
            except asyncio.TimeoutError:pass
    deaths=set()
    if g.mafia_votes:
        cnt=Counter(g.mafia_votes.values());mx=max(cnt.values());deaths.add(random.choice([u for u,v in cnt.items() if v==mx]))
    if g.commissioner_kill_target:deaths.add(g.commissioner_kill_target)
    if g.doctor_target in deaths:deaths.remove(g.doctor_target)
    for u in deaths:g.alive.discard(u)
    if deaths:await gm(bot,g,"☀️ <b>УТРО</b>\n\nПогибли: "+", ".join(esc(g,u) for u in deaths),parse_mode="HTML")
    else:await gm(bot,g,"☀️ <b>УТРО</b>\n\nЭтой ночью никто не погиб.",parse_mode="HTML")
    for u in deaths:await last_word(bot,g,u)
async def last_word(bot,g,u):
    if is_bot(u):return
    g.phase="last_word";g.last_word_player=u;g.action_event.clear();await gm(bot,g,f"🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n{esc(g,u)} — {g.last_word_seconds} сек.",parse_mode="HTML")
    try:await asyncio.wait_for(g.action_event.wait(),g.last_word_seconds)
    except asyncio.TimeoutError:pass
    g.last_word_used.add(u);g.last_word_player=None
async def day(bot,g):
    g.phase="day_discussion";g.day_number+=1;await gm(bot,g,f"☀️ <b>ДЕНЬ {g.day_number}</b>\n\n💬 Обсуждение — {g.discussion_seconds//60} мин.",parse_mode="HTML");await asyncio.sleep(g.discussion_seconds)
    g.phase="day_vote";g.reset_day_votes();items=[(u,g.player_names.get(u,"Игрок")) for u in g.alive];await gm(bot,g,"🗳 <b>ГОЛОСОВАНИЕ</b>\n\nКто мафия?",reply_markup=vote_k(g.chat_id,items),parse_mode="HTML")
    for u in g.alive:
        if is_bot(u):
            choices=[x for x in g.alive if x!=u];t=choose(g,choices,CIVILIAN)
            if t:g.day_votes[u]=t
    g.action_event.clear()
    if g.all_day_votes_complete():g.action_event.set()
    try:await asyncio.wait_for(g.action_event.wait(),g.vote_seconds)
    except asyncio.TimeoutError:pass
    for u in g.alive:
        if u not in g.day_votes:
            choices=[x for x in g.alive if x!=u]
            if choices:g.day_votes[u]=random.choice(choices)
    cnt=Counter(g.day_votes.values())
    if not cnt:return
    mx=max(cnt.values());leaders=[u for u,v in cnt.items() if v==mx]
    if len(leaders)>1:await gm(bot,g,"⚖️ <b>НИЧЬЯ</b>\n\nНикто не изгнан.",parse_mode="HTML");return
    out=leaders[0];g.alive.discard(out);await gm(bot,g,f"🔴 <b>{esc(g,out)}</b> покидает игру.",parse_mode="HTML");await last_word(bot,g,out)
async def finish(bot,g,w):
    g.started=False;g.phase="finished";await gm(bot,g,"🏆 <b>ИГРА ОКОНЧЕНА</b>\n\n"+("🔫 МАФИЯ ПОБЕДИЛА!" if w=="mafia" else "🏆 ГОРОД ПОБЕДИЛ!"),parse_mode="HTML")
@dp.message(Command("restart"))
async def restart(m:Message,bot:Bot):
    if m.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP) or not await _is_admin(bot,m.chat.id,m.from_user.id):return
    g=games.get(m.chat.id)
    if not g or not g.can_start():await m.answer("❌ Нужно минимум 4 игрока.");return
    t=tasks.get(m.chat.id)
    if t and not t.done():t.cancel()
    g.restart();tasks[m.chat.id]=asyncio.create_task(run_game(bot,g));await m.answer("🔄 Игра перезапущена.")
@dp.message(Command("stop"))
async def group_stop(m:Message,bot:Bot):
    if m.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP) or not await _is_admin(bot,m.chat.id,m.from_user.id):return
    g=games.get(m.chat.id)
    if g:g.stop()
    t=tasks.get(m.chat.id)
    if t and not t.done():t.cancel()
    await cleanup(bot,m.chat.id);await m.answer("⏹ Игра остановлена.")
async def main():
    global BOT_USERNAME
    bot=Bot(BOT_TOKEN);BOT_USERNAME=(await bot.get_me()).username;await bot.delete_my_commands()
    await bot.set_my_commands([BotCommand(command="mafia",description="Начать Мафию"),BotCommand(command="stop",description="Остановить игру"),BotCommand(command="restart",description="Перезапустить игру"),BotCommand(command="bots",description="Настроить ботов"),BotCommand(command="bots_off",description="Отключить ботов")],scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands([BotCommand(command="start",description="Запустить бота"),BotCommand(command="reset",description="Сбросить бота"),BotCommand(command="stop",description="Остановить бота")],scope=BotCommandScopeAllPrivateChats())
    print("🟢 Mafia Bot запущен")
    try:await dp.start_polling(bot)
    finally:await bot.session.close()
if __name__=="__main__":asyncio.run(main())
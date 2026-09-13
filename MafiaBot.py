import asyncio,html,os,random
from collections import Counter
from dotenv import load_dotenv
from aiogram import Bot,Dispatcher,F
from aiogram.filters import Command,CommandStart
from aiogram.enums import ChatType,ChatMemberStatus
from aiogram.types import Message,CallbackQuery,ChatMemberUpdated,BotCommand,BotCommandScopeAllGroupChats,BotCommandScopeAllPrivateChats,InlineKeyboardMarkup,InlineKeyboardButton
from game import Game,MAFIA,DOCTOR,COMMISSIONER,ROLE_NAMES,ROLE_DESCRIPTIONS
load_dotenv();BOT_TOKEN=os.getenv('BOT_TOKEN')
if not BOT_TOKEN:raise RuntimeError('Не найден BOT_TOKEN')
dp=Dispatcher();games={};game_messages={};tracked={};private_messages={};game_tasks={};BOT_USERNAME=None;bot_settings={};BOT_PREFIX=-10000000000

def is_bot(u):return u<0
def bot_id(cid,i):return BOT_PREFIX-(cid*100+i)
def name(g,u):return html.escape(g.player_names.get(u,'Игрок'))
def track(cid,mid,uid=None):
 tracked.setdefault(cid,set()).add(mid)
 if uid is not None:private_messages.setdefault(cid,set()).add((uid,mid))
async def ds(bot,cid,mid):
 try:await bot.delete_message(cid,mid)
 except Exception:pass
async def cg(bot,cid):
 for m in set(tracked.pop(cid,set())):await ds(bot,cid,m)
 for u,m in set(private_messages.pop(cid,set())):await ds(bot,u,m)
 game_messages.pop(cid,None)
async def sg(bot,g,text,**kw):
 m=await bot.send_message(g.chat_id,text,**kw);track(g.chat_id,m.message_id);return m
async def sp(bot,g,u,text,**kw):
 m=await bot.send_message(u,text,**kw);track(g.chat_id,m.message_id,u);return m
async def adm(bot,cid,u):
 try:return (await bot.get_chat_member(cid,u)).status in (ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.CREATOR)
 except Exception:return False

def lobby_kb(g):
 r=[[InlineKeyboardButton(text=f'🎮 Я ИГРАЮ • {len(g.players)}',callback_data='join')]]
 if g.can_start():r.append([InlineKeyboardButton(text='▶️ НАЧАТЬ ИГРУ',callback_data='start')])
 r.append([InlineKeyboardButton(text='🤖 БОТЫ',callback_data='bots'),InlineKeyboardButton(text='⚙️ НАСТРОЙКИ',callback_data='settings')]);return InlineKeyboardMarkup(inline_keyboard=r)
def game_kb(g):
 r=[[InlineKeyboardButton(text='🎭 МОЯ РОЛЬ',callback_data=f'role:{g.chat_id}')]]
 if BOT_USERNAME:r.append([InlineKeyboardButton(text='🎭 МОЙ НОЧНОЙ ХОД',url=f'https://t.me/{BOT_USERNAME}?start=game_{g.chat_id}')])
 return InlineKeyboardMarkup(inline_keyboard=r)
def bots_kb(cid):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🤖 1',callback_data=f'bc:{cid}:1'),InlineKeyboardButton(text='🤖 3',callback_data=f'bc:{cid}:3'),InlineKeyboardButton(text='🤖 6',callback_data=f'bc:{cid}:6'),InlineKeyboardButton(text='🤖 9',callback_data=f'bc:{cid}:9')],[InlineKeyboardButton(text='🟢 ЛЕГКО',callback_data=f'bd:{cid}:easy'),InlineKeyboardButton(text='🟡 СРЕДНЕ',callback_data=f'bd:{cid}:medium'),InlineKeyboardButton(text='🔴 СЛОЖНО',callback_data=f'bd:{cid}:hard')],[InlineKeyboardButton(text='❌ ОТКЛЮЧИТЬ БОТОВ',callback_data=f'bo:{cid}')],[InlineKeyboardButton(text='⬅️ НАЗАД',callback_data='bb')]])
def botst(cid):
 s=bot_settings.get(cid,{'count':0,'difficulty':'medium'});return f'🤖 <b>БОТЫ</b>\n\nКоличество: <b>{s["count"]}</b>\nСложность: <b>{{"easy":"🟢 Легко","medium":"🟡 Средне","hard":"🔴 Сложно"}[s["difficulty"]]}</b>'
def settings_kb(g):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'💬 {x} мин.',callback_data=f'disc:{g.chat_id}:{x}') for x in (2,4,6)],[InlineKeyboardButton(text='💬 8 мин.',callback_data=f'disc:{g.chat_id}:8'),InlineKeyboardButton(text='💬 10 мин.',callback_data=f'disc:{g.chat_id}:10')],[InlineKeyboardButton(text='🌙 Ночь −',callback_data=f'night:{g.chat_id}:-'),InlineKeyboardButton(text='🌙 Ночь +',callback_data=f'night:{g.chat_id}:+')],[InlineKeyboardButton(text='🔴 Слово −',callback_data=f'word:{g.chat_id}:-'),InlineKeyboardButton(text='🔴 Слово +',callback_data=f'word:{g.chat_id}:+')],[InlineKeyboardButton(text='⬅️ НАЗАД',callback_data='sb')]])
def settings_text(g):return f'⚙️ <b>НАСТРОЙКИ</b>\n\n💬 Обсуждение: <b>{g.discussion_seconds//60} мин.</b>\n🌙 Ночь: <b>{g.night_seconds//60} мин.</b>\n🔴 Последнее слово: <b>{g.last_word_seconds} сек.</b>'
def ltext(g):
 p='\n'.join(f'{i}. {"🤖" if is_bot(u) else "🟢"} {name(g,u)}' for i,u in enumerate(g.players,1)) or 'Пока никто не присоединился.';n=max(0,4-len(g.players));return f'🎭 <b>MAFIA — {html.escape(g.group_title)}</b>\n\n👥 Участников: <b>{len(g.players)}</b>\n\n{p}\n\n'+(f'⏳ Нужно ещё: <b>{n}</b>' if n else '✅ <b>Минимум игроков набран!</b>')
def stext(g):return f'🎭 <b>MAFIA — {html.escape(g.group_title)}</b>\n\n👥 Всего: <b>{len(g.players)}</b>\n🟢 Живых: <b>{len(g.alive)}</b>\n\n🌙 Ночь: <b>{g.night_number}</b>  ☀️ День: <b>{g.day_number}</b>\n<b>{g.phase}</b>'

def syncbots(g,n):
 humans=[u for u in g.players if not is_bot(u)];want=min(max(n,0),12-len(humans));current=[u for u in g.players if is_bot(u)]
 for u in current[want:]:g.players.remove(u);g.player_names.pop(u,None);g.player_usernames.pop(u,None)
 current=[u for u in g.players if is_bot(u)]
 names=['Алексей','Макс','Виктор','Данияр','Илья','Руслан','Сергей','Тимур','Артур']
 for i in range(1,want+1):
  u=bot_id(g.chat_id,i)
  if u not in current:g.players.append(u);g.player_names[u]=random.choice(names)+' 🤖';g.player_usernames[u]=''
 g.bot_count=want
async def update_main(bot,g):
 mid=game_messages.get(g.chat_id)
 if not mid:return
 try:await bot.edit_message_text(stext(g) if g.started else ltext(g),chat_id=g.chat_id,message_id=mid,reply_markup=game_kb(g) if g.started else lobby_kb(g),parse_mode='HTML')
 except Exception:pass

@dp.message(CommandStart())
async def start(m:Message,bot:Bot):
 global BOT_USERNAME;BOT_USERNAME=(await bot.get_me()).username
 if m.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP):return
 a=(m.text or '').split(maxsplit=1)[1] if ' ' in (m.text or '') else ''
 if a.startswith('game_'):
  try:g=games.get(int(a[5:]))
  except:g=None
  if g and g.started and m.from_user.id in g.alive:await private_action(bot,g,m.from_user.id);return
 await m.answer('🎭 <b>MAFIA</b>\n\nЛичный режим активирован. Роль и секретные действия доступны только вам.',parse_mode='HTML')
@dp.message(Command('reset'))
async def reset(m:Message):
 if m.chat.type==ChatType.PRIVATE:await m.answer('♻️ Личный режим сброшен. Нажмите /start для активации.')
@dp.message(Command('stop'))
async def stop(m:Message,bot:Bot):
 if m.chat.type==ChatType.PRIVATE:await m.answer('⏹ Личный режим остановлен.');return
 if not await adm(bot,m.chat.id,m.from_user.id):await m.answer('⚠️ Только администратор группы.');return
 cid=m.chat.id;t=game_tasks.get(cid)
 if t and not t.done():t.cancel()
 g=games.get(cid)
 if g:g.stop()
 await cg(bot,cid);games.pop(cid,None)
@dp.message(Command('bots'))
async def bots_cmd(m:Message,bot:Bot):
 if m.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP) or not await adm(bot,m.chat.id,m.from_user.id):return
 await m.answer(botst(m.chat.id),reply_markup=bots_kb(m.chat.id),parse_mode='HTML')
@dp.message(Command('bots_off'))
async def bots_off_cmd(m:Message,bot:Bot):
 if m.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP) or not await adm(bot,m.chat.id,m.from_user.id):return
 cid=m.chat.id;bot_settings.setdefault(cid,{'count':0,'difficulty':'medium'})['count']=0
 if cid in games and not games[cid].started:syncbots(games[cid],0);await update_main(bot,games[cid])
@dp.message(Command('restart'))
async def restart(m:Message,bot:Bot):
 if m.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP) or not await adm(bot,m.chat.id,m.from_user.id):return
 cid=m.chat.id;g=games.get(cid)
 if not g:await m.answer('❌ Нет игры. Используйте /mafia.');return
 t=game_tasks.get(cid)
 if t and not t.done():t.cancel()
 await cg(bot,cid);g.reset_to_lobby(True);m2=await bot.send_message(cid,ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML');game_messages[cid]=m2.message_id;track(cid,m2.message_id)
@dp.message(Command('mafia'))
async def mafia(m:Message,bot:Bot):
 if m.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP) or not await adm(bot,m.chat.id,m.from_user.id):return
 cid=m.chat.id
 if cid in games and games[cid].started:await m.answer('⚠️ Игра уже идёт.');return
 await cg(bot,cid);g=Game(cid,m.from_user.id,m.chat.title or 'MAFIA');s=bot_settings.get(cid,{'count':0,'difficulty':'medium'});g.bot_count=s['count'];g.bot_difficulty=s['difficulty'];games[cid]=g;syncbots(g,g.bot_count);m2=await bot.send_message(cid,ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML');game_messages[cid]=m2.message_id;track(cid,m2.message_id)
@dp.my_chat_member()
async def added(e:ChatMemberUpdated,bot:Bot):
 if e.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP) and e.new_chat_member.status in (ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR) and e.old_chat_member.status in (ChatMemberStatus.LEFT,ChatMemberStatus.KICKED):await bot.send_message(e.chat.id,'🎭 <b>MAFIA</b>\n\nБот должен быть администратором с правом удаления сообщений. После этого используйте /mafia.',parse_mode='HTML')
@dp.callback_query(F.data=='join')
async def join(c:CallbackQuery):
 g=games.get(c.message.chat.id);u=c.from_user.id
 if not g or g.started or u in g.players:await c.answer('❌ Нельзя присоединиться.',show_alert=True);return
 if len(g.players)>=12:await c.answer('❌ Максимум 12.',show_alert=True);return
 g.add_player(u);g.player_names[u]=c.from_user.full_name or 'Игрок';g.player_usernames[u]='@'+c.from_user.username if c.from_user.username else '';await c.message.edit_text(ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML');await c.answer('🎮 Вы в игре!')
@dp.callback_query(F.data=='start')
async def start_game(c:CallbackQuery,bot:Bot):
 cid=c.message.chat.id;g=games.get(cid)
 if not await adm(bot,cid,c.from_user.id) or not g or not g.can_start():await c.answer('❌ Нужно минимум 4 участника.',show_alert=True);return
 g.start();g.assign_roles();await c.message.edit_text('🎲 <b>ИГРА НАЧИНАЕТСЯ</b>\n\n🔒 Роли распределены.',parse_mode='HTML');await task(bot,g);await c.answer('🎭 Игра началась!')
@dp.callback_query(F.data=='bots')
async def bm(c:CallbackQuery,bot:Bot):
 if await adm(bot,c.message.chat.id,c.from_user.id):await c.message.edit_text(botst(c.message.chat.id),reply_markup=bots_kb(c.message.chat.id),parse_mode='HTML')
@dp.callback_query(F.data.startswith('bc:'))
async def bc(c:CallbackQuery,bot:Bot):
 _,cid,n=c.data.split(':');cid=int(cid);n=int(n)
 if not await adm(bot,cid,c.from_user.id):return
 s=bot_settings.setdefault(cid,{'count':0,'difficulty':'medium'});s['count']=n;g=games.get(cid)
 if g and not g.started:syncbots(g,n);await c.message.edit_text(ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML')
 else:await c.message.edit_text(botst(cid),reply_markup=bots_kb(cid),parse_mode='HTML')
 await c.answer()
@dp.callback_query(F.data.startswith('bd:'))
async def bd(c:CallbackQuery,bot:Bot):
 _,cid,d=c.data.split(':');cid=int(cid)
 if not await adm(bot,cid,c.from_user.id):return
 bot_settings.setdefault(cid,{'count':0,'difficulty':'medium'})['difficulty']=d;g=games.get(cid)
 if g and not g.started:g.bot_difficulty=d
 await c.message.edit_text(botst(cid),reply_markup=bots_kb(cid),parse_mode='HTML');await c.answer()
@dp.callback_query(F.data.startswith('bo:'))
async def bo(c:CallbackQuery,bot:Bot):
 cid=int(c.data.split(':')[1])
 if await adm(bot,cid,c.from_user.id):
  bot_settings.setdefault(cid,{'count':0,'difficulty':'medium'})['count']=0;g=games.get(cid)
  if g and not g.started:syncbots(g,0);await c.message.edit_text(ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML')
@dp.callback_query(F.data=='bb')
async def bb(c:CallbackQuery):
 g=games.get(c.message.chat.id)
 if g:await c.message.edit_text(ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML')
@dp.callback_query(F.data=='settings')
async def settings(c:CallbackQuery,bot:Bot):
 if await adm(bot,c.message.chat.id,c.from_user.id) and games.get(c.message.chat.id):await c.message.edit_text(settings_text(games[c.message.chat.id]),reply_markup=settings_kb(games[c.message.chat.id]),parse_mode='HTML')
@dp.callback_query(F.data.startswith('disc:'))
async def disc(c:CallbackQuery,bot:Bot):
 _,cid,v=c.data.split(':');cid=int(cid);g=games.get(cid)
 if g and await adm(bot,cid,c.from_user.id):g.discussion_seconds=int(v)*60;await c.message.edit_text(settings_text(g),reply_markup=settings_kb(g),parse_mode='HTML')
@dp.callback_query(F.data.startswith('night:'))
async def nightset(c:CallbackQuery,bot:Bot):
 _,cid,o=c.data.split(':');cid=int(cid);g=games.get(cid)
 if g and await adm(bot,cid,c.from_user.id):g.night_seconds=max(60,min(300,g.night_seconds+(60 if o=='+' else -60)));await c.message.edit_text(settings_text(g),reply_markup=settings_kb(g),parse_mode='HTML')
@dp.callback_query(F.data.startswith('word:'))
async def wordset(c:CallbackQuery,bot:Bot):
 _,cid,o=c.data.split(':');cid=int(cid);g=games.get(cid)
 if g and await adm(bot,cid,c.from_user.id):g.last_word_seconds=max(10,min(60,g.last_word_seconds+(10 if o=='+' else -10)));await c.message.edit_text(settings_text(g),reply_markup=settings_kb(g),parse_mode='HTML')
@dp.callback_query(F.data=='sb')
async def sb(c:CallbackQuery):
 g=games.get(c.message.chat.id)
 if g:await c.message.edit_text(ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML')

async def task(bot,g):
 old=game_tasks.get(g.chat_id)
 if old and not old.done():old.cancel()
 game_tasks[g.chat_id]=asyncio.create_task(run(bot,g))
async def run(bot,g):
 try:
  while g.started:
   await run_night(bot,g)
   if not g.started:break
   w=g.winner()
   if w:await finish(bot,g,w);break
   await run_day(bot,g)
   if not g.started:break
   w=g.winner()
   if w:await finish(bot,g,w);break
 except asyncio.CancelledError:raise
 except Exception as e:print('GAME ERROR',repr(e))

def choices(g,u,r):
 if r==MAFIA:return [(x,g.player_names.get(x,'Игрок')) for x in g.alive if x!=u and g.roles.get(x)!=MAFIA]
 if r==DOCTOR:return [(x,g.player_names.get(x,'Игрок')) for x in g.alive if x not in g.doctor_healed]
 return [(x,g.player_names.get(x,'Игрок')) for x in g.alive if x!=u]
def target_kb(cid,items,prefix):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(n),callback_data=f'{prefix}:{cid}:{u}')] for u,n in items])
async def private_action(bot,g,u):
 r=g.roles.get(u)
 if r==MAFIA:await sp(bot,g,u,'🔫 <b>ХОД МАФИИ</b>\n\nКого устранить?',reply_markup=target_kb(g.chat_id,choices(g,u,r),'mt'),parse_mode='HTML')
 elif r==DOCTOR:await sp(bot,g,u,'💊 <b>ХОД ДОКТОРА</b>\n\nКого спасти?',reply_markup=target_kb(g.chat_id,choices(g,u,r),'dt'),parse_mode='HTML')
 elif r==COMMISSIONER:await sp(bot,g,u,'🔎 <b>ХОД КОМИССАРА</b>\n\nВыберите игрока для проверки.',reply_markup=target_kb(g.chat_id,choices(g,u,r),'ct'),parse_mode='HTML')
async def botthink(g,u,kind,items):
 await asyncio.sleep({'easy':.3,'medium':.6,'hard':.9}.get(g.bot_difficulty,.6));ids=[x[0] for x in items]
 if not ids:return None
 if kind=='mafia':return random.choice([x for x in ids if g.roles.get(x)!=MAFIA] or ids)
 if kind=='vote':
  alive=[x for x in ids if x!=u and x in g.alive];
  if not alive:return None
  if g.bot_difficulty=='easy':return random.choice(alive)
  return max(alive,key=lambda x:Counter(g.day_votes.values()).get(x,0)+random.random()*3+(2 if g.bot_difficulty=='hard' and g.roles.get(x)==MAFIA else 0))
 return random.choice(ids)
async def run_night(bot,g):
 g.phase='night';g.night_number+=1;g.reset_night_actions();await update_main(bot,g);await sg(bot,g,'🌙 <b>ГОРОД ЗАСЫПАЕТ</b>\n\n🔫 <b>Мафия выбирает жертву...</b>',parse_mode='HTML')
 for u in list(g.alive_mafia()):
  if is_bot(u):g.mafia_votes[u]=await botthink(g,u,'mafia',choices(g,u,MAFIA))
  else:await private_action(bot,g,u)
 if g.alive_mafia():
  g.action_event.clear()
  try:await asyncio.wait_for(g.action_event.wait(),g.night_seconds)
  except asyncio.TimeoutError:pass
 await sg(bot,g,'💊 <b>Доктор хочет спасти...</b>',parse_mode='HTML');d=next((u for u in g.alive if g.roles.get(u)==DOCTOR),None)
 if d:
  if is_bot(d):g.doctor_target=await botthink(g,d,'doctor',choices(g,d,DOCTOR))
  else:await private_action(bot,g,d)
  if g.doctor_target is None:
   g.action_event.clear()
   try:await asyncio.wait_for(g.action_event.wait(),g.night_seconds)
   except asyncio.TimeoutError:pass
 c=next((u for u in g.alive if g.roles.get(u)==COMMISSIONER),None)
 if c:
  await sg(bot,g,'🔎 <b>Комиссар совершает ночное действие...</b>',parse_mode='HTML')
  if is_bot(c):g.commissioner_target=await botthink(g,c,'check',choices(g,c,COMMISSIONER));
  else:await private_action(bot,g,c)
 deaths=resolve(g)
 if deaths:await sg(bot,g,'☀️ <b>ГОРОД ПРОСЫПАЕТСЯ</b>\n\nПогибли: '+', '.join(name(g,u) for u in deaths),parse_mode='HTML');[await lastword(bot,g,u) for u in deaths]
 else:await sg(bot,g,'☀️ <b>ГОРОД ПРОСЫПАЕТСЯ</b>\n\nЭтой ночью никто не погиб.',parse_mode='HTML')
def resolve(g):
 d=[];mv=Counter(g.mafia_votes.values());m=mv.most_common(1)[0][0] if mv else None
 if m in g.alive and m!=g.doctor_target:d.append(m)
 if g.commissioner_kill_target in g.alive and g.commissioner_kill_target!=g.doctor_target:d.append(g.commissioner_kill_target)
 for u in d:g.alive.discard(u)
 if g.doctor_target is not None:g.doctor_healed.add(g.doctor_target)
 return list(dict.fromkeys(d))
async def lastword(bot,g,u):
 if u in g.last_word_used:return
 g.last_word_used.add(u);g.last_word_player=u;g.phase='last_word';await sg(bot,g,f'🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n<b>{name(g,u)}</b> — {g.last_word_seconds} сек.',parse_mode='HTML')
 if is_bot(u):g.action_event.set();return
 g.action_event.clear()
 try:await asyncio.wait_for(g.action_event.wait(),g.last_word_seconds)
 except asyncio.TimeoutError:pass
async def run_day(bot,g):
 g.day_number+=1;g.phase='day_discussion';await update_main(bot,g);m=await sg(bot,g,'☀️ <b>ОБСУЖДЕНИЕ</b>\n\n⏱ Осталось: <b>%02d:00</b>'%(g.discussion_seconds//60),parse_mode='HTML')
 for left in range(g.discussion_seconds-1,-1,-1):
  if not g.started:return
  try:await bot.edit_message_text(f'☀️ <b>ОБСУЖДЕНИЕ</b>\n\n⏱ Осталось: <b>{left//60:02d}:{left%60:02d}</b>',chat_id=g.chat_id,message_id=m.message_id,parse_mode='HTML')
  except Exception:pass
  await asyncio.sleep(1)
 await vote_phase(bot,g)
async def vote_phase(bot,g,candidates=None):
 g.phase='day_vote';g.reset_day_votes();ids=candidates or g.alive_players();m=await sg(bot,g,'🗳 <b>ГОЛОСОВАНИЕ</b>\n\nКто за кого голосует?\n\nПока голосов нет.',reply_markup=vote_kb(g),parse_mode='HTML');g.vote_message_id=m.message_id;g.action_event.clear()
 for u in list(g.alive):
  if is_bot(u):
   t=await botthink(g,u,'vote',[(x,g.player_names.get(x,'Игрок')) for x in ids if x in g.alive and x!=u])
   if t:g.day_votes[u]=t;await vote_update(bot,g)
 if len(g.day_votes)<len(g.alive):
  try:await asyncio.wait_for(g.action_event.wait(),g.vote_seconds)
  except asyncio.TimeoutError:pass
 for u in g.alive_players():
  if u not in g.day_votes:
   ch=[x for x in ids if x in g.alive and x!=u]
   if ch:g.day_votes[u]=random.choice(ch)
 await vote_update(bot,g,True);cnt=Counter(g.day_votes.values())
 if not cnt:return
 hi=max(cnt.values());leaders=[u for u,c in cnt.items() if c==hi]
 if len(leaders)>1 and candidates is None:g.tie_candidates=leaders;await sg(bot,g,'⚖️ <b>НИЧЬЯ</b>\n\nРешающее голосование.',parse_mode='HTML');await vote_phase(bot,g,leaders);return
 if len(leaders)>1:await sg(bot,g,'⚖️ <b>СНОВА НИЧЬЯ</b>\n\nНикто не изгнан.',parse_mode='HTML');return
 out=leaders[0];g.alive.discard(out);await sg(bot,g,f'🔴 <b>{name(g,out)}</b> набрал больше всего голосов и покидает игру.',parse_mode='HTML');await lastword(bot,g,out)
def vote_kb(g):
 c=Counter(g.day_votes.values());ids=g.tie_candidates or g.alive_players();return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'🗳 {g.player_names.get(u,"Игрок")} — {c.get(u,0)}',callback_data=f'vote:{g.chat_id}:{u}')] for u in ids if u in g.alive])
async def vote_update(bot,g,final=False):
 c=Counter(g.day_votes.values());lines=['🗳 <b>РЕЗУЛЬТАТЫ ГОЛОСОВАНИЯ</b>','','Кто за кого проголосовал:']+[f'• {name(g,v)} → <b>{name(g,t)}</b>' for v,t in g.day_votes.items()]+['','Текущий счёт:']+[f'• {name(g,u)} — <b>{n}</b>' for u,n in c.most_common()]
 try:await bot.edit_message_text('\n'.join(lines),chat_id=g.chat_id,message_id=g.vote_message_id,reply_markup=None if final else vote_kb(g),parse_mode='HTML')
 except Exception:pass
@dp.callback_query(F.data.startswith('vote:'))
async def vote(c:CallbackQuery,bot:Bot):
 _,cid,t=c.data.split(':');cid=int(cid);t=int(t);g=games.get(cid);u=c.from_user.id
 if not g or g.phase!='day_vote' or u not in g.alive or t not in g.alive or u==t:await c.answer('❌ Голосование недоступно.',show_alert=True);return
 g.day_votes[u]=t;await vote_update(bot,g);await c.answer(f'Вы проголосовали за {g.player_names.get(t,"Игрок")}');
 if len(g.day_votes)>=len(g.alive):g.action_event.set()
@dp.callback_query(F.data.startswith('role:'))
async def role(c:CallbackQuery):
 g=games.get(int(c.data.split(':')[1]));r=g.roles.get(c.from_user.id) if g else None
 if r:await c.answer(f'{ROLE_NAMES[r]}\n\n{ROLE_DESCRIPTIONS[r]}',show_alert=True)
@dp.callback_query(F.data.startswith('mt:'))
async def mt(c:CallbackQuery,bot:Bot):await nt(c,bot,MAFIA)
@dp.callback_query(F.data.startswith('dt:'))
async def dt(c:CallbackQuery,bot:Bot):await nt(c,bot,DOCTOR)
async def nt(c,bot,r):
 _,cid,t=c.data.split(':');cid=int(cid);t=int(t);g=games.get(cid);u=c.from_user.id
 if not g or g.phase!='night' or g.roles.get(u)!=r or t not in g.alive:await c.answer('❌ Действие недоступно.',show_alert=True);return
 if r==MAFIA and g.roles.get(t)==MAFIA:await c.answer('Нельзя выбрать союзника.',show_alert=True);return
 if r==DOCTOR and t in g.doctor_healed:await c.answer('Этого игрока уже лечили.',show_alert=True);return
 if r==MAFIA:g.mafia_votes[u]=t;await sg(bot,g,'🔫 <b>Мафия выбирает жертву...</b>',parse_mode='HTML')
 else:g.doctor_target=t;await sg(bot,g,'💊 <b>Доктор хочет спасти...</b>',parse_mode='HTML')
 await c.answer(f'Выбор принят: {g.player_names.get(t,"Игрок")}');
 if r==MAFIA and len(g.mafia_votes)>=len(g.alive_mafia()):g.action_event.set()
 if r==DOCTOR:g.action_event.set()
@dp.callback_query(F.data.startswith('ct:'))
async def ct(c:CallbackQuery,bot:Bot):
 _,cid,t=c.data.split(':');cid=int(cid);t=int(t);g=games.get(cid);u=c.from_user.id
 if not g or g.roles.get(u)!=COMMISSIONER or t not in g.alive:await c.answer('❌ Недоступно.',show_alert=True);return
 r=g.roles[t];txt={'mafia':'🔴 МАФИЯ','doctor':'💊 ДОКТОР','commissioner':'🔎 КОМИССАР','civilian':'🟢 МИРНЫЙ ЖИТЕЛЬ'}[r];g.commissioner_target=t;await c.answer(f'{name(g,t)}: {txt}',show_alert=True);await sp(bot,g,u,f'🔎 <b>ПРОВЕРКА</b>\n\n{name(g,t)} → <b>{txt}</b>',parse_mode='HTML')
@dp.message()
async def messages(m:Message,bot:Bot):
 g=games.get(m.chat.id) if m.chat.type in (ChatType.GROUP,ChatType.SUPERGROUP) else None
 if not g:return
 u=m.from_user.id
 if g.started and u in g.players and u not in g.alive:await ds(bot,m.chat.id,m.message_id);return
 if g.phase=='last_word' and u==g.last_word_player:
  await ds(bot,m.chat.id,m.message_id);await sg(bot,g,f'🔴 <b>ПОСЛЕДНЕЕ СЛОВО</b>\n\n{name(g,u)}\n\n«{html.escape(m.text or "") }»',parse_mode='HTML');g.action_event.set()
async def finish(bot,g,w):
 g.started=False;g.phase='finished';await update_main(bot,g);await sg(bot,g,('🔫 <b>МАФИЯ ПОБЕДИЛА!</b>' if w=='mafia' else '🏆 <b>ГОРОД ПОБЕДИЛ!</b>'),parse_mode='HTML');await asyncio.sleep(3);await cg(bot,g.chat_id);g.reset_to_lobby(False);games[g.chat_id]=g;m=await bot.send_message(g.chat_id,ltext(g),reply_markup=lobby_kb(g),parse_mode='HTML');game_messages[g.chat_id]=m.message_id;track(g.chat_id,m.message_id)
async def main():
 global BOT_USERNAME
 bot=Bot(BOT_TOKEN);BOT_USERNAME=(await bot.get_me()).username;await bot.delete_my_commands();await bot.set_my_commands([BotCommand(command='mafia',description='Создать лобби'),BotCommand(command='bots',description='Настройки ботов'),BotCommand(command='bots_off',description='Отключить ботов'),BotCommand(command='stop',description='Остановить игру'),BotCommand(command='restart',description='Начать набор заново')],scope=BotCommandScopeAllGroupChats());await bot.set_my_commands([BotCommand(command='start',description='Активировать бота'),BotCommand(command='reset',description='Сбросить личный режим'),BotCommand(command='stop',description='Остановить личный режим')],scope=BotCommandScopeAllPrivateChats());print('Mafia Bot started');await dp.start_polling(bot)
if __name__=='__main__':asyncio.run(main())
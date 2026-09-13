from dataclasses import dataclass, field
import asyncio
import random

MAFIA="mafia"; DOCTOR="doctor"; COMMISSIONER="commissioner"; CIVILIAN="civilian"
ROLE_NAMES={MAFIA:"🔫 Мафия",DOCTOR:"💊 Доктор",COMMISSIONER:"🔎 Комиссар",CIVILIAN:"👤 Мирный житель"}
ROLE_DESCRIPTIONS={MAFIA:"Ты — МАФИЯ.\n\nНочью выбери игрока, которого необходимо устранить.",DOCTOR:"Ты — ДОКТОР.\n\nНочью выбери игрока, которого попытаешься спасти.",COMMISSIONER:"Ты — КОМИССАР.\n\nНочью проверь одного игрока и узнай его роль. Также можешь совершить убийство.",CIVILIAN:"Ты — МИРНЫЙ ЖИТЕЛЬ.\n\nТвоя задача — вычислить мафию во время обсуждения и голосования."}
ROLE_HISTORY={}
@dataclass
class Game:
    chat_id:int; creator_id:int; group_title:str="MAFIA"
    players:list[int]=field(default_factory=list); player_names:dict[int,str]=field(default_factory=dict); player_usernames:dict[int,str]=field(default_factory=dict); roles:dict[int,str]=field(default_factory=dict); alive:set[int]=field(default_factory=set)
    last_word_used:set[int]=field(default_factory=set); doctor_healed:set[int]=field(default_factory=set); started:bool=False; phase:str="lobby"; night_number:int=0; day_number:int=0; last_word_player:int|None=None; last_word_text:str|None=None
    mafia_votes:dict[int,int]=field(default_factory=dict); doctor_target:int|None=None; commissioner_target:int|None=None; commissioner_kill_target:int|None=None; day_votes:dict[int,int]=field(default_factory=dict); day_vote_selection:dict[int,int]=field(default_factory=dict); vote_message_id:int|None=None; tie_candidates:list[int]=field(default_factory=list)
    action_event:asyncio.Event=field(default_factory=asyncio.Event,repr=False); discussion_seconds:int=120; night_seconds:int=120; last_word_seconds:int=30; vote_seconds:int=120; MIN_PLAYERS=4
    def add_player(self,user_id):
        if self.started or user_id in self.players:return False
        self.players.append(user_id);return True
    def can_start(self):return len(self.players)>=self.MIN_PLAYERS
    def start(self):
        if not self.can_start():return False
        self.started=True;self.phase="starting";self.night_number=0;self.day_number=0;self.last_word_used.clear();self.doctor_healed.clear();self.last_word_player=None;self.last_word_text=None;self.action_event.clear();return True
    def restart(self):
        if not self.can_start():return False
        self.roles.clear();self.alive.clear();self.last_word_used.clear();self.doctor_healed.clear();self.last_word_player=None;self.last_word_text=None;self.mafia_votes.clear();self.doctor_target=None;self.commissioner_target=None;self.commissioner_kill_target=None;self.day_votes.clear();self.day_vote_selection.clear();self.vote_message_id=None;self.tie_candidates.clear();self.action_event.clear();self.night_number=0;self.day_number=0;self.started=True;self.phase="starting";return True
    def reset_to_lobby(self,keep_players=False):
        self.started=False;self.phase="lobby";self.roles.clear();self.alive.clear();self.last_word_used.clear();self.doctor_healed.clear();self.last_word_player=None;self.last_word_text=None;self.mafia_votes.clear();self.doctor_target=None;self.commissioner_target=None;self.commissioner_kill_target=None;self.day_votes.clear();self.day_vote_selection.clear();self.vote_message_id=None;self.tie_candidates.clear();self.action_event.set();self.night_number=0;self.day_number=0
        if not keep_players:self.players.clear();self.player_names.clear();self.player_usernames.clear()
    def stop(self):self.started=False;self.phase="stopped";self.action_event.set()
    def alive_players(self):return [u for u in self.players if u in self.alive]
    def alive_mafia(self):return [u for u in self.alive if self.roles.get(u)==MAFIA]
    def alive_citizens(self):return [u for u in self.alive if self.roles.get(u)!=MAFIA]
    def winner(self):
        m=len(self.alive_mafia());c=len(self.alive_citizens())
        if m==0:return "citizens"
        if m>=c:return "mafia"
        return None
    def reset_night_actions(self):self.mafia_votes.clear();self.doctor_target=None;self.commissioner_target=None;self.commissioner_kill_target=None;self.action_event.clear()
    def reset_night(self):self.reset_night_actions()
    def reset_day_votes(self):self.day_votes.clear();self.day_vote_selection.clear();self.vote_message_id=None;self.action_event.clear()
    def role_distribution(self):
        d={4:[MAFIA,DOCTOR,CIVILIAN,CIVILIAN],5:[MAFIA,DOCTOR,CIVILIAN,CIVILIAN,CIVILIAN],6:[MAFIA,MAFIA,COMMISSIONER,DOCTOR,CIVILIAN,CIVILIAN],7:[MAFIA,MAFIA,COMMISSIONER,DOCTOR,CIVILIAN,CIVILIAN,CIVILIAN],8:[MAFIA,MAFIA,COMMISSIONER,DOCTOR,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN],9:[MAFIA,MAFIA,MAFIA,COMMISSIONER,DOCTOR,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN],10:[MAFIA,MAFIA,MAFIA,COMMISSIONER,DOCTOR,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN],11:[MAFIA,MAFIA,MAFIA,COMMISSIONER,DOCTOR,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN],12:[MAFIA,MAFIA,MAFIA,COMMISSIONER,DOCTOR,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN,CIVILIAN]}
        if len(self.players) in d:return d[len(self.players)].copy()
        mc=max(3,round(len(self.players)*.25));r=[MAFIA]*mc+[DOCTOR,COMMISSIONER]
        while len(r)<len(self.players):r.append(CIVILIAN)
        return r[:len(self.players)]
    def assign_roles(self):
        roles=self.role_distribution();random.shuffle(roles);available=self.players.copy();self.roles.clear()
        for role in roles:
            weighted=[]
            for u in available:
                h=ROLE_HISTORY.get(u,[]);w=1/(1+h.count(role)*3)+random.uniform(0,.35);weighted.append((u,w))
            total=sum(w for _,w in weighted);pick=random.uniform(0,total);cur=0;selected=available[-1]
            for u,w in weighted:
                cur+=w
                if cur>=pick:selected=u;break
            self.roles[selected]=role;available.remove(selected)
        self.alive=set(self.players)
        for u,r in self.roles.items():
            h=ROLE_HISTORY.setdefault(u,[]);h.append(r)
            if len(h)>5:del h[:-5]
    def mafia_allies(self,user_id):return [u for u in self.players if u!=user_id and self.roles.get(u)==MAFIA] if self.roles.get(user_id)==MAFIA else []
    def doctor_targets(self):return [u for u in self.alive if u not in self.doctor_healed]
    def night_actions_complete(self):
        if len(self.mafia_votes)<len(self.alive_mafia()):return False
        doctor=next((u for u in self.alive if self.roles.get(u)==DOCTOR),None)
        if doctor is not None and self.doctor_targets() and self.doctor_target is None:return False
        return True
    def all_day_votes_complete(self):return len(self.day_votes)>=len(self.alive)
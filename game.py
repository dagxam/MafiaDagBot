from dataclasses import dataclass, field
import asyncio
import random

MAFIA = "mafia"
DOCTOR = "doctor"
COMMISSIONER = "commissioner"
CIVILIAN = "civilian"

ROLE_NAMES = {
    MAFIA: "🔫 Мафия",
    DOCTOR: "💊 Доктор",
    COMMISSIONER: "🔎 Комиссар",
    CIVILIAN: "👤 Мирный житель",
}

ROLE_DESCRIPTIONS = {
    MAFIA: "Ты — МАФИЯ.\n\nНочью выбери игрока, которого необходимо устранить.",
    DOCTOR: "Ты — ДОКТОР.\n\nНочью выбери игрока, которого попытаешься спасти.",
    COMMISSIONER: "Ты — КОМИССАР.\n\nНочью выбери одно действие: проверить игрока и узнать его роль ИЛИ совершить убийство.",
    CIVILIAN: "Ты — МИРНЫЙ ЖИТЕЛЬ.\n\nТвоя задача — вычислить мафию во время обсуждения и голосования.",
}

ROLE_HISTORY: dict[int, list[str]] = {}


@dataclass
class Game:
    chat_id: int
    creator_id: int
    group_title: str = "MAFIA"
    players: list[int] = field(default_factory=list)
    player_names: dict[int, str] = field(default_factory=dict)
    roles: dict[int, str] = field(default_factory=dict)
    alive: set[int] = field(default_factory=set)
    last_word_used: set[int] = field(default_factory=set)
    doctor_healed: set[int] = field(default_factory=set)
    started: bool = False
    phase: str = "lobby"
    night_number: int = 0
    day_number: int = 0
    last_word_player: int | None = None
    last_word_text: str | None = None
    mafia_votes: dict[int, int] = field(default_factory=dict)
    doctor_target: int | None = None
    commissioner_target: int | None = None
    commissioner_kill_target: int | None = None
    mafia_kill_target: int | None = None
    vote_message_id: int | None = None
    day_votes: dict[int, int] = field(default_factory=dict)
    day_vote_selection: dict[int, int] = field(default_factory=dict)
    tie_candidates: list[int] = field(default_factory=list)
    action_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    discussion_seconds: int = 120
    night_seconds: int = 15
    last_word_seconds: int = 15
    vote_seconds: int = 10
    bot_count: int = 0
    bot_difficulty: str = "medium"
    MIN_PLAYERS = 4

    def add_player(self, user_id: int) -> bool:
        if self.started or user_id in self.players:
            return False
        self.players.append(user_id)
        return True

    def can_start(self) -> bool:
        return len(self.players) >= self.MIN_PLAYERS

    def start(self) -> bool:
        if not self.can_start():
            return False
        self.started = True
        self.phase = "starting"
        self.night_number = 0
        self.day_number = 0
        self.last_word_used.clear()
        self.doctor_healed.clear()
        self.last_word_player = None
        self.last_word_text = None
        return True

    def restart(self) -> bool:
        if not self.can_start():
            return False
        self.roles.clear()
        self.alive.clear()
        self.last_word_used.clear()
        self.doctor_healed.clear()
        self.last_word_player = None
        self.last_word_text = None
        self.mafia_votes.clear()
        self.doctor_target = None
        self.commissioner_target = None
        self.commissioner_kill_target = None
        self.mafia_kill_target = None
        self.vote_message_id = None
        self.day_votes.clear()
        self.day_vote_selection.clear()
        self.tie_candidates.clear()
        self.action_event.clear()
        self.night_number = 0
        self.day_number = 0
        self.started = True
        self.phase = "starting"
        return True

    def reset_to_lobby(self, keep_players: bool = False):
        self.started = False
        self.phase = "lobby"
        self.roles.clear()
        self.alive.clear()
        self.last_word_used.clear()
        self.doctor_healed.clear()
        self.last_word_player = None
        self.last_word_text = None
        self.mafia_votes.clear()
        self.doctor_target = None
        self.commissioner_target = None
        self.commissioner_kill_target = None
        self.mafia_kill_target = None
        self.vote_message_id = None
        self.day_votes.clear()
        self.day_vote_selection.clear()
        self.tie_candidates.clear()
        self.action_event.set()
        self.night_number = 0
        self.day_number = 0
        if not keep_players:
            self.players.clear()
            self.player_names.clear()

    def stop(self):
        self.started = False
        self.phase = "stopped"
        self.action_event.set()

    def alive_players(self) -> list[int]:
        return [uid for uid in self.players if uid in self.alive]

    def alive_mafia(self) -> list[int]:
        return [uid for uid in self.alive if self.roles.get(uid) == MAFIA]

    def alive_citizens(self) -> list[int]:
        return [uid for uid in self.alive if self.roles.get(uid) != MAFIA]

    def winner(self) -> str | None:
        mafia_count = len(self.alive_mafia())
        citizens_count = len(self.alive_citizens())
        if mafia_count == 0:
            return "citizens"
        if mafia_count >= citizens_count:
            return "mafia"
        return None

    def reset_night_actions(self):
        self.mafia_votes.clear()
        self.doctor_target = None
        self.commissioner_target = None
        self.commissioner_kill_target = None
        self.action_event.clear()

    def reset_day_votes(self):
        self.day_votes.clear()
        self.day_vote_selection.clear()
        self.action_event.clear()

    def role_distribution(self):
        count = len(self.players)
        distributions = {
            4: [MAFIA, DOCTOR, CIVILIAN, CIVILIAN],
            5: [MAFIA, DOCTOR, CIVILIAN, CIVILIAN, CIVILIAN],
            6: [MAFIA, MAFIA, COMMISSIONER, DOCTOR, CIVILIAN, CIVILIAN],
            7: [MAFIA, MAFIA, COMMISSIONER, DOCTOR, CIVILIAN, CIVILIAN, CIVILIAN],
            8: [MAFIA, MAFIA, COMMISSIONER, DOCTOR, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN],
            9: [MAFIA, MAFIA, MAFIA, COMMISSIONER, DOCTOR, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN],
            10: [MAFIA, MAFIA, MAFIA, COMMISSIONER, DOCTOR, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN],
            11: [MAFIA, MAFIA, MAFIA, COMMISSIONER, DOCTOR, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN],
            12: [MAFIA, MAFIA, MAFIA, COMMISSIONER, DOCTOR, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN, CIVILIAN],
        }
        if count in distributions:
            return distributions[count].copy()
        mafia_count = max(3, round(count * 0.25))
        roles = [MAFIA] * mafia_count + [DOCTOR, COMMISSIONER]
        while len(roles) < count:
            roles.append(CIVILIAN)
        return roles[:count]

    def assign_roles(self):
        roles = self.role_distribution()
        random.shuffle(roles)
        available = self.players.copy()
        self.roles.clear()
        for role in roles:
            if not available:
                break
            weighted = []
            for uid in available:
                history = ROLE_HISTORY.get(uid, [])
                recent_same = history.count(role)
                weight = 1.0 / (1.0 + recent_same * 3.0)
                weight += random.uniform(0.0, 0.35)
                weighted.append((uid, weight))
            total = sum(weight for _, weight in weighted)
            pick = random.uniform(0, total)
            current = 0
            selected = available[-1]
            for uid, weight in weighted:
                current += weight
                if current >= pick:
                    selected = uid
                    break
            self.roles[selected] = role
            available.remove(selected)
        self.alive = set(self.players)
        for uid, role in self.roles.items():
            history = ROLE_HISTORY.setdefault(uid, [])
            history.append(role)
            if len(history) > 5:
                del history[:-5]

    def mafia_allies(self, user_id: int) -> list[int]:
        if self.roles.get(user_id) != MAFIA:
            return []
        return [uid for uid in self.players if uid != user_id and self.roles.get(uid) == MAFIA]

    def doctor_targets(self) -> list[int]:
        return [uid for uid in self.alive if uid not in self.doctor_healed]

    def night_actions_complete(self) -> bool:
        mafia = self.alive_mafia()
        if len(self.mafia_votes) < len(mafia):
            return False
        doctor = next((uid for uid in self.alive if self.roles.get(uid) == DOCTOR), None)
        if doctor is not None and self.doctor_targets() and self.doctor_target is None:
            return False
        commissioner = next((uid for uid in self.alive if self.roles.get(uid) == COMMISSIONER), None)
        if commissioner is not None and self.commissioner_target is None and self.commissioner_kill_target is None:
            return False
        return True

    def all_day_votes_complete(self) -> bool:
        return len(self.day_votes) >= len(self.alive)

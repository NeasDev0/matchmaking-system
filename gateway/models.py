from pydantic import BaseModel

from .config import TEAM_SIZE


class ServerRegisterRequest(BaseModel):
    server_id: str
    ip: str
    port: int
    max_players: int = TEAM_SIZE


class ServerHeartbeatRequest(BaseModel):
    server_id: str
    current_players: int
    status: str = "waiting"  # waiting | reserved | in_game


class FindMatchRequest(BaseModel):
    player_id: str
    mmr: int


class AcceptMatchRequest(BaseModel):
    player_id: str
    lobby_id: str


class ConfirmMatchRequest(BaseModel):
    server_id: str
    lobby_id: str

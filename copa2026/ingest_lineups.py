"""
Copa 2026 AI — Ingestão de Escalações Simuladas
Busca a última escalação conhecida de cada seleção na football-data.org
e insere como simulação para os jogos da Copa 2026

Uso:
    python ingest_lineups.py
"""

import os
import time
import logging
from dotenv import load_dotenv

import requests
from supabase import create_client

load_dotenv(override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("copa2026-lineups")

FOOTBALL_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")
SUPABASE_URL     = os.getenv("SUPABASE_URL")
SUPABASE_KEY     = os.getenv("SUPABASE_SERVICE_KEY")

BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": FOOTBALL_API_KEY}

# Mapeamento time football-data.org → Copa 2026
# external_id da football-data.org para cada seleção
TEAM_IDS = {
    "Brasil":          76,
    "Argentina":       762,
    "França":          773,
    "Inglaterra":      66,
    "Alemanha":        759,
    "Espanha":         760,
    "Portugal":        765,
    "Holanda":         779,
    "Uruguai":         80,
    "México":          764,
    "Estados Unidos":  768,
    "Colômbia":        63,
    "Japão":           796,
    "Coreia do Sul":   732,
    "Marrocos":        1031,
    "Senegal":         907,
    "Austrália":       772,
    "Suíça":           788,
    "Croácia":         799,
    "Bélgica":         805,
}

# Competições para buscar últimos jogos (da mais recente para mais antiga)
COMPETITIONS = ["CL", "WC", "EC", "EURO", "WCQ"]


def api_get(path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{BASE_URL}{path}",
        headers=HEADERS,
        params=params,
        timeout=15
    )
    if resp.status_code == 429:
        log.warning("Rate limit — aguardando 60s")
        time.sleep(60)
        return api_get(path, params)
    if not resp.ok:
        log.error(f"Erro API: {resp.status_code} — {resp.text[:100]}")
        return {}
    return resp.json()


def get_last_lineup(team_id: int) -> dict:
    """Busca a última partida finalizada do time e retorna a escalação."""
    data = api_get(f"/teams/{team_id}/matches", params={
        "status": "FINISHED",
        "limit":  5,
    })

    matches = data.get("matches", [])
    if not matches:
        return {}

    # Pega o mais recente
    last_match = matches[-1]
    match_id   = last_match["id"]

    log.info(f"Buscando escalação do jogo {match_id}: "
             f"{last_match['homeTeam']['name']} vs {last_match['awayTeam']['name']}")

    match_data = api_get(f"/matches/{match_id}")
    return match_data


def position_from_role(position: str, index: int, total: int) -> tuple:
    """Gera coordenadas aproximadas baseadas na posição."""
    positions_map = {
        "Goalkeeper":  (5, 50),
        "Defender":    (25, 20 + (index * 20)),
        "Midfielder":  (50, 15 + (index * 18)),
        "Forward":     (75, 25 + (index * 25)),
        "Attacker":    (75, 25 + (index * 25)),
    }
    return positions_map.get(position, (50, 50))


def ingest_team_lineup(sb, team_name: str, team_id: int, copa_matches: list):
    """Busca última escalação do time e insere para todos os jogos da Copa."""
    log.info(f"Processando {team_name}...")

    match_data = get_last_lineup(team_id)
    if not match_data:
        log.warning(f"Sem dados para {team_name}")
        return 0

    # Identifica qual time é o nosso
    home = match_data.get("homeTeam", {})
    away = match_data.get("awayTeam", {})

    if home.get("id") == team_id:
        team_data = home
    else:
        team_data = away

    formation  = team_data.get("formation", "4-3-3")
    coach      = team_data.get("coach", {}).get("name", "")
    starters   = team_data.get("startXI", [])
    subs       = team_data.get("substitutes", [])

    if not starters:
        log.warning(f"Sem escalação disponível para {team_name}")
        return 0

    total = 0

    # Busca o external_id do time na tabela teams
    team_result = sb.table("teams").select("external_id").eq("name", team_name).limit(1).execute()
    if not team_result.data:
        log.warning(f"Time não encontrado no banco: {team_name}")
        return 0

    db_team_id = team_result.data[0]["external_id"]

    # Insere para cada jogo da Copa onde esse time participa
    team_matches = [
        m for m in copa_matches
        if m["home_team_id"] == db_team_id or m["away_team_id"] == db_team_id
    ]

    log.info(f"{team_name}: {len(starters)} titulares, {len(subs)} reservas, {len(team_matches)} jogos na Copa")

    rows = []

    # Titulares
    defenders  = [p for p in starters if p.get("player", {}).get("position") == "Defender"]
    midfielders = [p for p in starters if p.get("player", {}).get("position") == "Midfielder"]
    forwards   = [p for p in starters if p.get("player", {}).get("position") in ["Forward", "Attacker"]]
    goalkeepers = [p for p in starters if p.get("player", {}).get("position") == "Goalkeeper"]

    for copa_match in team_matches:
        match_id = copa_match["external_id"]

        for p in starters:
            player   = p.get("player", {})
            position = player.get("position", "")

            # Coordenadas baseadas na posição
            if position == "Goalkeeper":
                x, y = 5, 50
            elif position == "Defender":
                idx = defenders.index(p) if p in defenders else 0
                x = 25
                y = 15 + (idx * (70 / max(len(defenders), 1)))
            elif position == "Midfielder":
                idx = midfielders.index(p) if p in midfielders else 0
                x = 50
                y = 15 + (idx * (70 / max(len(midfielders), 1)))
            else:
                idx = forwards.index(p) if p in forwards else 0
                x = 75
                y = 20 + (idx * (60 / max(len(forwards), 1)))

            rows.append({
                "match_id":     match_id,
                "team_id":      db_team_id,
                "team_name":    team_name,
                "formation":    formation,
                "player_id":    player.get("id"),
                "player_name":  player.get("name"),
                "shirt_number": player.get("shirtNumber"),
                "position":     position,
                "position_x":   round(x, 1),
                "position_y":   round(y, 1),
                "is_starter":   True,
                "coach_name":   coach,
                "is_official":  False,
                "source":       "simulation",
            })

        # Reservas
        for p in subs:
            player = p.get("player", {})
            rows.append({
                "match_id":     match_id,
                "team_id":      db_team_id,
                "team_name":    team_name,
                "formation":    formation,
                "player_id":    player.get("id"),
                "player_name":  player.get("name"),
                "shirt_number": player.get("shirtNumber"),
                "position":     player.get("player", {}).get("position", ""),
                "position_x":   None,
                "position_y":   None,
                "is_starter":   False,
                "coach_name":   coach,
                "is_official":  False,
                "source":       "simulation",
            })

    if rows:
        sb.table("lineups").upsert(
            rows, on_conflict="match_id, team_id, player_id"
        ).execute()
        total += len(rows)
        log.info(f"{team_name}: {len(rows)} registros inseridos")

    time.sleep(6)  # respeita rate limit da API (10 req/min)
    return total


def run():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    log.info("Conexão Supabase OK")

    # Busca todos os jogos da Copa
    matches_result = sb.table("matches").select(
        "external_id, home_team_id, away_team_id, home_team_name, away_team_name"
    ).execute()
    copa_matches = matches_result.data or []
    log.info(f"Jogos na Copa: {len(copa_matches)}")

    total = 0
    for team_name, team_id in TEAM_IDS.items():
        try:
            count = ingest_team_lineup(sb, team_name, team_id, copa_matches)
            total += count
        except Exception as e:
            log.error(f"Erro em {team_name}: {e}")

    log.info(f"Total de registros inseridos: {total}")


if __name__ == "__main__":
    run()
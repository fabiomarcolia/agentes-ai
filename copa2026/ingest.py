"""
Copa do Mundo 2026 — Agente de Ingestão de Dados
Fonte: football-data.org (free tier)
Destino: Supabase
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timezone
from typing import Optional

import time
import requests
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv(override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("copa2026")

FOOTBALL_API_KEY  = os.getenv("FOOTBALL_DATA_API_KEY")
SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_KEY      = os.getenv("SUPABASE_SERVICE_KEY")
COMPETITION_CODE  = "WC"
BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": FOOTBALL_API_KEY}

# Cache de nomes traduzidos
_team_names_cache = {}

def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL e SUPABASE_SERVICE_KEY são obrigatórios no .env")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def api_get(path: str, params: dict = None) -> dict:
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if resp.status_code == 429:
        log.warning("Rate limit atingido. Aguarde 1 minuto e tente novamente.")
        raise RuntimeError("Rate limit: football-data.org permite 10 req/min no free tier")
    resp.raise_for_status()
    return resp.json()

def load_team_names(sb: Client):
    """Carrega nomes traduzidos dos times do banco."""
    global _team_names_cache
    result = sb.table("teams").select("external_id, name").execute()
    _team_names_cache = {t["external_id"]: t["name"] for t in (result.data or [])}
    log.info(f"Cache de nomes carregado: {len(_team_names_cache)} times")

def get_team_name(team_id: int, fallback: str) -> str:
    """Retorna o nome traduzido do time ou o fallback."""
    return _team_names_cache.get(team_id, fallback)

def ingest_teams(sb: Client) -> int:
    log.info("Buscando times da competição...")
    data = api_get(f"/competitions/{COMPETITION_CODE}/teams")

    teams = []
    for t in data.get("teams", []):
        # Mantém nome PT-BR se já existir no banco
        existing_name = _team_names_cache.get(t["id"])
        teams.append({
            "external_id":  t["id"],
            "name":         existing_name or t["name"],  # usa PT-BR se disponível
            "short_name":   t.get("shortName"),
            "tla":          t.get("tla"),
            "country":      t.get("area", {}).get("name"),
            "crest_url":    t.get("crest"),
        })

    if not teams:
        log.warning("Nenhum time retornado pela API")
        return 0

    result = sb.table("teams").upsert(teams, on_conflict="external_id").execute()
    log.info(f"Times upserted: {len(teams)}")
    return len(teams)

def ingest_matches(sb: Client) -> int:
    log.info("Buscando partidas...")
    data = api_get(f"/competitions/{COMPETITION_CODE}/matches")

    matches_to_upsert = []
    goals_to_insert   = []
    bookings_to_insert = []

    for m in data.get("matches", []):
        score  = m.get("score", {})
        ft     = score.get("fullTime", {})
        ht     = score.get("halfTime", {})

        home_id = m["homeTeam"]["id"]
        away_id = m["awayTeam"]["id"]

        match_row = {
            "external_id":    m["id"],
            "utc_date":       m.get("utcDate"),
            "status":         m.get("status"),
            "stage":          m.get("stage"),
            "group_name":     m.get("group"),
            "matchday":       m.get("matchday"),
            "home_team_id":   home_id,
            "away_team_id":   away_id,
            # USA NOME DO BANCO (PT-BR) em vez da API (inglês)
            "home_team_name": get_team_name(home_id, m["homeTeam"]["name"]),
            "away_team_name": get_team_name(away_id, m["awayTeam"]["name"]),
            "home_score":     ft.get("home"),
            "away_score":     ft.get("away"),
            "home_score_ht":  ht.get("home"),
            "away_score_ht":  ht.get("away"),
            "winner":         score.get("winner"),
            "last_updated":   m.get("lastUpdated"),
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        }
        matches_to_upsert.append(match_row)

        if m.get("status") == "FINISHED":
            for g in m.get("goals", []):
                team_id = g.get("team", {}).get("id")
                goals_to_insert.append({
                    "match_id":     m["id"],
                    "minute":       g.get("minute"),
                    "extra_time":   g.get("injuryTime"),
                    "type":         g.get("type"),
                    "team_id":      team_id,
                    "team_name":    get_team_name(team_id, g.get("team", {}).get("name", "")),
                    "scorer_name":  g.get("scorer", {}).get("name"),
                    "scorer_id":    g.get("scorer", {}).get("id"),
                    "assist_name":  g.get("assist", {}).get("name") if g.get("assist") else None,
                    "assist_id":    g.get("assist", {}).get("id")   if g.get("assist") else None,
                })

            for b in m.get("bookings", []):
                team_id = b.get("team", {}).get("id")
                bookings_to_insert.append({
                    "match_id":     m["id"],
                    "minute":       b.get("minute"),
                    "type":         b.get("card"),
                    "team_id":      team_id,
                    "team_name":    get_team_name(team_id, b.get("team", {}).get("name", "")),
                    "player_name":  b.get("player", {}).get("name"),
                    "player_id":    b.get("player", {}).get("id"),
                })

    if matches_to_upsert:
        sb.table("matches").upsert(matches_to_upsert, on_conflict="external_id").execute()
        log.info(f"Partidas upserted: {len(matches_to_upsert)}")

    if goals_to_insert:
        finished_ids = [m["external_id"] for m in matches_to_upsert if m["status"] == "FINISHED"]
        sb.table("goals").delete().in_("match_id", finished_ids).execute()
        sb.table("goals").insert(goals_to_insert).execute()
        log.info(f"Gols inseridos: {len(goals_to_insert)}")

    if bookings_to_insert:
        finished_ids = [m["external_id"] for m in matches_to_upsert if m["status"] == "FINISHED"]
        sb.table("bookings").delete().in_("match_id", finished_ids).execute()
        sb.table("bookings").insert(bookings_to_insert).execute()
        log.info(f"Cartões inseridos: {len(bookings_to_insert)}")

    return len(matches_to_upsert)

def ingest_scorers(sb: Client) -> int:
    log.info("Buscando artilheiros...")
    data = api_get(f"/competitions/{COMPETITION_CODE}/scorers", params={"limit": 50})

    scorers = []
    for s in data.get("scorers", []):
        p = s.get("player", {})
        t = s.get("team", {})
        team_id = t.get("id")
        scorers.append({
            "player_id":      p.get("id"),
            "player_name":    p.get("name"),
            "nationality":    p.get("nationality"),
            "team_id":        team_id,
            "team_name":      get_team_name(team_id, t.get("name", "")),
            "goals":          s.get("goals", 0),
            "assists":        s.get("assists", 0),
            "penalties":      s.get("penalties", 0),
            "played_matches": s.get("playedMatches", 0),
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        })

    if not scorers:
        log.warning("Nenhum artilheiro retornado ainda")
        return 0

    sb.table("top_scorers").upsert(scorers, on_conflict="player_id").execute()
    log.info(f"Artilheiros upserted: {len(scorers)}")
    return len(scorers)

def log_ingest(sb: Client, source: str, status: str, count: int, error: Optional[str] = None):
    sb.table("ingest_log").insert({
        "source":            source,
        "status":            status,
        "records_upserted":  count,
        "error_message":     error,
    }).execute()

def ingest_lineups(sb: Client) -> int:
    """Captura escalações dos jogos finalizados ou em andamento."""
    result = (
        sb.table("matches")
        .select("external_id")
        .in_("status", ["FINISHED", "IN_PLAY"])
        .execute()
    )

    if not result.data:
        log.info("Nenhum jogo finalizado para buscar escalações.")
        return 0

    total = 0
    for match in result.data:
        match_id = match["external_id"]
        try:
            data = api_get(f"/matches/{match_id}")
            home_lineup = data.get("homeTeam", {})
            away_lineup = data.get("awayTeam", {})

            rows = []
            for team_data in [home_lineup, away_lineup]:
                team_id   = team_data.get("id")
                formation = team_data.get("formation")
                coach     = team_data.get("coach", {}).get("name")
                team_name = get_team_name(team_id, team_data.get("name", ""))

                for p in team_data.get("startXI", []):
                    player = p.get("player", {})
                    rows.append({
                        "match_id":     match_id,
                        "team_id":      team_id,
                        "team_name":    team_name,
                        "formation":    formation,
                        "player_id":    player.get("id"),
                        "player_name":  player.get("name"),
                        "shirt_number": player.get("shirtNumber"),
                        "position":     player.get("position"),
                        "is_starter":   True,
                        "is_official":  True,
                        "coach_name":   coach,
                        "source":       "official",
                    })

                for p in team_data.get("substitutes", []):
                    player = p.get("player", {})
                    rows.append({
                        "match_id":     match_id,
                        "team_id":      team_id,
                        "team_name":    team_name,
                        "formation":    formation,
                        "player_id":    player.get("id"),
                        "player_name":  player.get("name"),
                        "shirt_number": player.get("shirtNumber"),
                        "position":     player.get("position"),
                        "is_starter":   False,
                        "is_official":  True,
                        "coach_name":   coach,
                        "source":       "official",
                    })

            if rows:
                sb.table("lineups").upsert(
                    rows, on_conflict="match_id, team_id, player_id"
                ).execute()
                total += len(rows)
                log.info(f"Escalação do jogo {match_id} salva: {len(rows)} jogadores")

            time.sleep(1)

        except Exception as e:
            log.warning(f"Erro ao buscar escalação do jogo {match_id}: {e}")

    log.info(f"Total escalações upserted: {total}")
    return total

def run(only: Optional[str] = None):
    if not FOOTBALL_API_KEY:
        raise ValueError("FOOTBALL_DATA_API_KEY não definido no .env")

    sb = get_supabase()
    log.info("Conexão com Supabase OK")

    # Carrega nomes PT-BR antes de tudo
    load_team_names(sb)

    tasks = {
        "teams":   (ingest_teams,   "teams"),
        "matches": (ingest_matches, "matches"),
        "scorers": (ingest_scorers, "scorers"),
        "lineups": (ingest_lineups, "lineups"),
    }

    if only:
        tasks = {k: v for k, v in tasks.items() if k == only}
        if not tasks:
            log.error(f"Opção inválida: {only}. Use: teams, matches, scorers, lineups")
            sys.exit(1)

    for task_name, (func, log_source) in tasks.items():
        try:
            count = func(sb)
            log_ingest(sb, log_source, "success", count)
        except Exception as e:
            log.error(f"Erro em {task_name}: {e}")
            log_ingest(sb, log_source, "error", 0, str(e))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingestão Copa 2026 → Supabase")
    parser.add_argument("--only", type=str, help="Rodar só uma tarefa: teams | matches | scorers | lineups")
    args = parser.parse_args()
    run(only=args.only)
"""
Copa do Mundo 2026 — Agente de Ingestão de Dados
Fonte: football-data.org (free tier)
Destino: Supabase

Uso:
    python ingest.py                  # roda tudo
    python ingest.py --only matches   # só partidas
    python ingest.py --only scorers   # só artilharia
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from supabase import create_client, Client
from dotenv import load_dotenv

# Carrega .env se existir (local). No GitHub Actions, as vars já estão no ambiente.
load_dotenv(override=False)

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("copa2026")

# ── Config ────────────────────────────────────────────────────
FOOTBALL_API_KEY  = os.getenv("FOOTBALL_DATA_API_KEY")
SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_KEY      = os.getenv("SUPABASE_SERVICE_KEY")   # service_role key (bypass RLS)

# ID da Copa do Mundo 2026 na football-data.org
# Confirmar em: https://api.football-data.org/v4/competitions
COMPETITION_CODE  = "WC"

BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": FOOTBALL_API_KEY}


# ── Supabase client ───────────────────────────────────────────
def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL e SUPABASE_SERVICE_KEY são obrigatórios no .env")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── API helpers ───────────────────────────────────────────────
def api_get(path: str, params: dict = None) -> dict:
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)

    if resp.status_code == 429:
        log.warning("Rate limit atingido. Aguarde 1 minuto e tente novamente.")
        raise RuntimeError("Rate limit: football-data.org permite 10 req/min no free tier")

    resp.raise_for_status()
    return resp.json()


# ── Ingestão: Times ──────────────────────────────────────────
def ingest_teams(sb: Client) -> int:
    log.info("Buscando times da competição...")
    data = api_get(f"/competitions/{COMPETITION_CODE}/teams")

    teams = []
    for t in data.get("teams", []):
        teams.append({
            "external_id":  t["id"],
            "name":         t["name"],
            "short_name":   t.get("shortName"),
            "tla":          t.get("tla"),
            "country":      t.get("area", {}).get("name"),
            "crest_url":    t.get("crest"),
        })

    if not teams:
        log.warning("Nenhum time retornado pela API")
        return 0

    result = sb.table("teams").upsert(teams, on_conflict="external_id").execute()
    count = len(teams)
    log.info(f"Times upserted: {count}")
    return count


# ── Ingestão: Partidas + Gols + Cartões ──────────────────────
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

        match_row = {
            "external_id":    m["id"],
            "utc_date":       m.get("utcDate"),
            "status":         m.get("status"),
            "stage":          m.get("stage"),
            "group_name":     m.get("group"),
            "matchday":       m.get("matchday"),
            "home_team_id":   m["homeTeam"]["id"],
            "away_team_id":   m["awayTeam"]["id"],
            "home_team_name": m["homeTeam"]["name"],
            "away_team_name": m["awayTeam"]["name"],
            "home_score":     ft.get("home"),
            "away_score":     ft.get("away"),
            "home_score_ht":  ht.get("home"),
            "away_score_ht":  ht.get("away"),
            "winner":         score.get("winner"),
            "last_updated":   m.get("lastUpdated"),
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        }
        matches_to_upsert.append(match_row)

        # Só extrai eventos de jogos finalizados
        if m.get("status") == "FINISHED":
            for g in m.get("goals", []):
                goals_to_insert.append({
                    "match_id":     m["id"],
                    "minute":       g.get("minute"),
                    "extra_time":   g.get("injuryTime"),
                    "type":         g.get("type"),
                    "team_id":      g.get("team", {}).get("id"),
                    "team_name":    g.get("team", {}).get("name"),
                    "scorer_name":  g.get("scorer", {}).get("name"),
                    "scorer_id":    g.get("scorer", {}).get("id"),
                    "assist_name":  g.get("assist", {}).get("name") if g.get("assist") else None,
                    "assist_id":    g.get("assist", {}).get("id")   if g.get("assist") else None,
                })

            for b in m.get("bookings", []):
                bookings_to_insert.append({
                    "match_id":     m["id"],
                    "minute":       b.get("minute"),
                    "type":         b.get("card"),
                    "team_id":      b.get("team", {}).get("id"),
                    "team_name":    b.get("team", {}).get("name"),
                    "player_name":  b.get("player", {}).get("name"),
                    "player_id":    b.get("player", {}).get("id"),
                })

    # Upsert partidas
    if matches_to_upsert:
        sb.table("matches").upsert(matches_to_upsert, on_conflict="external_id").execute()
        log.info(f"Partidas upserted: {len(matches_to_upsert)}")

    # Insert gols (deleta e recria para manter consistência)
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


# ── Ingestão: Artilharia ─────────────────────────────────────
def ingest_scorers(sb: Client) -> int:
    log.info("Buscando artilheiros...")
    data = api_get(
        f"/competitions/{COMPETITION_CODE}/scorers",
        params={"limit": 50}
    )

    scorers = []
    for s in data.get("scorers", []):
        p = s.get("player", {})
        t = s.get("team", {})
        scorers.append({
            "player_id":      p.get("id"),
            "player_name":    p.get("name"),
            "nationality":    p.get("nationality"),
            "team_id":        t.get("id"),
            "team_name":      t.get("name"),
            "goals":          s.get("goals", 0),
            "assists":        s.get("assists", 0),
            "penalties":      s.get("penalties", 0),
            "played_matches": s.get("playedMatches", 0),
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        })

    if not scorers:
        log.warning("Nenhum artilheiro retornado ainda (torneio pode não ter começado)")
        return 0

    result = sb.table("top_scorers").upsert(scorers, on_conflict="player_id").execute()
    log.info(f"Artilheiros upserted: {len(scorers)}")
    return len(scorers)


# ── Log de execução ───────────────────────────────────────────
def log_ingest(sb: Client, source: str, status: str, count: int, error: Optional[str] = None):
    sb.table("ingest_log").insert({
        "source":            source,
        "status":            status,
        "records_upserted":  count,
        "error_message":     error,
    }).execute()


# ── Main ──────────────────────────────────────────────────────
def run(only: Optional[str] = None):
    if not FOOTBALL_API_KEY:
        raise ValueError("FOOTBALL_DATA_API_KEY não definido no .env")

    sb = get_supabase()
    log.info("Conexão com Supabase OK")

    tasks = {
        "teams":   (ingest_teams,   "teams"),
        "matches": (ingest_matches, "matches"),
        "scorers": (ingest_scorers, "scorers"),
    }

    # Se --only foi passado, roda só aquele
    if only:
        tasks = {k: v for k, v in tasks.items() if k == only}
        if not tasks:
            log.error(f"Opção inválida: {only}. Use: teams, matches, scorers")
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
    parser.add_argument("--only", type=str, help="Rodar só uma tarefa: teams | matches | scorers")
    args = parser.parse_args()

    run(only=args.only)

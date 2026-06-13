"""
Copa 2026 AI — Ingestão de Gols via ESPN API
Gratuito, sem chave, dados em tempo real
Uso: python ingest_espn.py
"""

import os
import logging
from datetime import datetime, timezone, timedelta

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("copa2026-espn")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

ESPN_BASE    = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"

# Mapeamento ESPN team ID → nome PT-BR (carregado do banco)
_team_map = {}

def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def load_team_map(sb):
    """Carrega mapa ESPN team name → external_id + nome PT-BR do banco."""
    result = sb.table("teams").select("external_id, name, tla").execute()
    for t in (result.data or []):
        _team_map[t["name"]] = t
    log.info(f"Mapa de times carregado: {len(_team_map)} times")

def get_espn_events(date_str: str) -> list:
    """Busca eventos de uma data no formato YYYYMMDD."""
    url = f"{ESPN_BASE}/scoreboard?dates={date_str}&limit=50"
    resp = requests.get(url, timeout=15)
    if not resp.ok:
        log.error(f"Erro ESPN: {resp.status_code}")
        return []
    return resp.json().get("events", [])

# Mapa de traduções ESPN → PT-BR
ESPN_TO_PT = {
    "mexico": "méxico", "south africa": "áfrica do sul",
    "south korea": "coreia do sul", "czechia": "república tcheca",
    "czech republic": "república tcheca", "united states": "estados unidos",
    "usa": "estados unidos", "france": "frança", "germany": "alemanha",
    "spain": "espanha", "england": "inglaterra", "netherlands": "holanda",
    "portugal": "portugal", "brazil": "brasil", "argentina": "argentina",
    "uruguay": "uruguai", "colombia": "colômbia", "ecuador": "equador",
    "paraguay": "paraguai", "switzerland": "suíça", "croatia": "croácia",
    "belgium": "bélgica", "denmark": "dinamarca", "sweden": "suécia",
    "poland": "polônia", "ukraine": "ucrânia", "turkey": "turquia",
    "serbia": "sérvia", "morocco": "marrocos", "senegal": "senegal",
    "nigeria": "nigéria", "cameroon": "camarões", "ghana": "gana",
    "japan": "japão", "australia": "austrália", "iran": "irã",
    "saudi arabia": "arábia saudita", "qatar": "catar",
    "canada": "canadá", "panama": "panamá", "honduras": "honduras",
    "costa rica": "costa rica", "jamaica": "jamaica",
    "bolivia": "bolívia", "chile": "chile", "peru": "peru",
    "venezuela": "venezuela", "greece": "grécia", "albania": "albânia",
    "scotland": "escócia", "romania": "romênia", "hungary": "hungria",
    "sloveniia": "eslovênia", "slovenia": "eslovênia",
    "new zealand": "nova zelândia", "indonesia": "indonésia",
    "egypt": "egito", "tanzania": "tanzânia", "kenya": "quênia",
    "angola": "angola", "togo": "togo", "cuba": "cuba",
    "guatemala": "guatemala", "haiti": "haiti",
    "trinidad and tobago": "trinidad e tobago", "philippines": "filipinas",
    "thailand": "tailândia", "china": "china", "china pr": "china",
    "bahrain": "bahrein", "algeria": "argélia",
    "ivory coast": "costa do marfim", "dr congo": "congo",
    "bosnia-herzegovina": "bósnia e herzegovina",
    "bosnia": "bosnia-herzegovina",
    "bósnia e herzegovina": "bosnia-herzegovina",
    "bosnia and herzegovina": "bosnia-herzegovina",
}

def normalize(name: str) -> str:
    """Normaliza nome para comparação."""
    n = name.lower().strip()
    return ESPN_TO_PT.get(n, n)

def find_match_id(sb, home_name: str, away_name: str, date_str: str) -> int:
    """Encontra o external_id do jogo no Supabase."""
    date = datetime.strptime(date_str, "%Y%m%d")
    start = datetime(date.year, date.month, date.day, 0, 0, tzinfo=timezone.utc).isoformat()
    end   = datetime(date.year, date.month, date.day, 23, 59, tzinfo=timezone.utc).isoformat()

    result = sb.table("matches").select("external_id, home_team_name, away_team_name").gte("utc_date", start).lte("utc_date", end).execute()

    home_pt = normalize(home_name)
    away_pt = normalize(away_name)

    for m in (result.data or []):
        h = m["home_team_name"].lower()
        a = m["away_team_name"].lower()
        # Match exato
        if (home_pt in h or h in home_pt) and (away_pt in a or a in away_pt):
            return m["external_id"]
        if (away_pt in h or h in away_pt) and (home_pt in a or a in home_pt):
            return m["external_id"]
        # Match parcial por palavra
        home_words = [w for w in home_pt.split() if len(w) > 3]
        away_words = [w for w in away_pt.split() if len(w) > 3]
        if any(w in h for w in home_words) and any(w in a for w in away_words):
            return m["external_id"]
        if any(w in a for w in home_words) and any(w in h for w in away_words):
            return m["external_id"]
    return None

def process_event(sb, event: dict):
    """Processa um evento ESPN e salva gols e cartões no Supabase."""
    comp   = event.get("competitions", [{}])[0]
    status = comp.get("status", {}).get("type", {})

    if not status.get("completed"):
        return  # só jogos finalizados

    # Times
    competitors = comp.get("competitors", [])
    home = next((c for c in competitors if c["homeAway"] == "home"), {})
    away = next((c for c in competitors if c["homeAway"] == "away"), {})

    home_name = home.get("team", {}).get("displayName", "")
    away_name = away.get("team", {}).get("displayName", "")

    # Data do evento
    date_str = event["date"][:8].replace("-", "")
    date_dt  = datetime.strptime(event["date"][:10], "%Y-%m-%d")
    date_str = date_dt.strftime("%Y%m%d")

    match_id = find_match_id(sb, home_name, away_name, date_str)
    if not match_id:
        log.warning(f"Jogo não encontrado no banco: {home_name} vs {away_name}")
        return

    log.info(f"Processando: {home_name} vs {away_name} (match_id={match_id})")

    # Mapa ESPN team_id → nome
    espn_teams = {}
    for c in competitors:
        espn_teams[c["team"]["id"]] = c["team"]["displayName"]

    # Limpa gols e cartões existentes para recriar
    sb.table("goals").delete().eq("match_id", match_id).execute()
    sb.table("bookings").delete().eq("match_id", match_id).execute()

    goals    = []
    bookings = []

    for detail in comp.get("details", []):
        tipo = detail.get("type", {}).get("text", "")
        clock = detail.get("clock", {})
        minute = int(clock.get("value", 0) / 60) if clock.get("value") else 0
        espn_team_id = detail.get("team", {}).get("id", "")
        team_name_en = espn_teams.get(espn_team_id, "")

        # Busca nome PT-BR
        team_name_pt = team_name_en
        for nome_pt, data in _team_map.items():
            if team_name_en.lower() in nome_pt.lower() or nome_pt.lower() in team_name_en.lower():
                team_name_pt = nome_pt
                break

        athletes = detail.get("athletesInvolved", [])
        scorer   = athletes[0] if athletes else {}

        if detail.get("scoringPlay"):
            goal_type = "REGULAR"
            if detail.get("ownGoal"):
                goal_type = "OWN_GOAL"
            elif detail.get("penaltyKick"):
                goal_type = "PENALTY"

            goals.append({
                "match_id":    match_id,
                "minute":      minute,
                "type":        goal_type,
                "team_name":   team_name_pt,
                "scorer_name": scorer.get("fullName", ""),
                "scorer_id":   int(scorer["id"]) if scorer.get("id") else None,
            })

        elif detail.get("yellowCard") or detail.get("redCard"):
            card_type = "RED_CARD" if detail.get("redCard") else "YELLOW_CARD"
            bookings.append({
                "match_id":    match_id,
                "minute":      minute,
                "type":        card_type,
                "team_name":   team_name_pt,
                "player_name": scorer.get("fullName", ""),
                "player_id":   int(scorer["id"]) if scorer.get("id") else None,
            })

    if goals:
        sb.table("goals").insert(goals).execute()
        log.info(f"Gols inseridos: {len(goals)}")

    if bookings:
        sb.table("bookings").insert(bookings).execute()
        log.info(f"Cartões inseridos: {len(bookings)}")


def run():
    sb = get_supabase()
    log.info("Conexão Supabase OK")
    load_team_map(sb)

    # Busca jogos dos últimos 3 dias + hoje
    today = datetime.now(timezone.utc)
    dates = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(4)]

    total_events = 0
    for date_str in dates:
        events = get_espn_events(date_str)
        log.info(f"Data {date_str}: {len(events)} eventos")
        for event in events:
            process_event(sb, event)
            total_events += 1

    log.info(f"Total processado: {total_events} eventos")

if __name__ == "__main__":
    run()
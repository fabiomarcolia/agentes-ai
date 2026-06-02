"""
Copa 2026 AI — Captura X/Twitter via API v2
Usa Bearer Token direto — sem Apify
Destino: Supabase

Uso:
    python social_capture.py              # captura + análise
    python social_capture.py --analyze    # só análise de sentimento
"""

import os
import re
import time
import json
import argparse
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("copa2026-social")

# ── Config ────────────────────────────────────────────────────
TWITTER_BEARER = os.getenv("TWITTER_BEARER_TOKEN")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_SERVICE_KEY")
GROQ_KEY       = os.getenv("GROQ_API_KEY")
GROK_KEY       = os.getenv("GROK_API_KEY")

TWITTER_BASE   = "https://api.twitter.com/2"

# Termos de busca — mistura hashtags e termos livres
SEARCH_TERMS = [
    "#Copa2026",
    "#WorldCup2026",
    "#WorldCup",
    "Copa do Mundo 2026",
    "#FIFAWorldCup",
]

# Termos por seleção (adicionados durante os jogos)
TEAM_TERMS = [
    ("#BRA OR #Brasil OR Brazil 2026", "Brazil"),
    ("#ARG OR Argentina 2026",         "Argentina"),
    ("#FRA OR France 2026",            "France"),
    ("#ENG OR England 2026",           "England"),
    ("#GER OR Germany 2026",           "Germany"),
    ("#ESP OR Spain 2026",             "Spain"),
    ("#POR OR Portugal 2026",          "Portugal"),
]

# Seleções para detectar no texto
TEAM_KEYWORDS = {
    "Brazil":      ["brazil", "brasil", "seleção", "canarinho", "#bra", "brasileira", "neymar", "vinicius"],
    "Argentina":   ["argentina", "albiceleste", "#arg", "messi", "scaloni"],
    "France":      ["france", "frança", "les bleus", "#fra", "mbappé", "mbappe", "deschamps"],
    "England":     ["england", "inglaterra", "three lions", "#eng", "kane"],
    "Germany":     ["germany", "alemanha", "mannschaft", "#ger", "#deu"],
    "Spain":       ["spain", "espanha", "la roja", "#esp", "morata"],
    "Portugal":    ["portugal", "#por", "ronaldo", "cr7", "martínez"],
    "Netherlands": ["netherlands", "holanda", "oranje", "#ned", "#nld"],
    "Uruguay":     ["uruguay", "uruguai", "celeste", "#uru"],
}


# ── Supabase ──────────────────────────────────────────────────
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Twitter API v2 ────────────────────────────────────────────
def twitter_search(query: str, max_results: int = 50) -> list:
    """Busca tweets recentes via API v2."""
    headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}

    params = {
        "query":       f"{query} -is:retweet lang:pt OR lang:en OR lang:es",
        "max_results": min(max_results, 100),
        "tweet.fields": "created_at,public_metrics,lang,entities",
        "user.fields":  "username,id",
        "expansions":   "author_id",
    }

    resp = requests.get(
        f"{TWITTER_BASE}/tweets/search/recent",
        headers=headers,
        params=params,
        timeout=20,
    )

    if resp.status_code == 429:
        log.warning("Rate limit atingido — aguardando 15 segundos")
        time.sleep(15)
        return []

    if not resp.ok:
        log.error(f"Erro Twitter API: {resp.status_code} — {resp.text[:200]}")
        return []

    data = resp.json()

    if "errors" in data and not data.get("data"):
        log.warning(f"Twitter API retornou erro: {data['errors']}")
        return []

    tweets    = data.get("data", [])
    users_map = {}

    # Mapeia users pelo ID
    for u in data.get("includes", {}).get("users", []):
        users_map[u["id"]] = u.get("username", "")

    log.info(f"Tweets retornados para '{query[:40]}': {len(tweets)}")
    return tweets, users_map


def capture_twitter(search_terms: list, max_per_term: int = 30) -> list:
    """Captura tweets para cada termo de busca."""
    if not TWITTER_BEARER:
        raise ValueError("TWITTER_BEARER_TOKEN não definido no .env")

    all_posts = []

    for term in search_terms:
        result = twitter_search(term, max_results=max_per_term)
        if not result:
            continue

        tweets, users_map = result

        for t in tweets:
            metrics = t.get("public_metrics", {})
            entities = t.get("entities", {})
            hashtags = [h["tag"].lower() for h in entities.get("hashtags", [])]

            post = {
                "platform":       "twitter",
                "post_id":        t["id"],
                "author":         users_map.get(t.get("author_id"), ""),
                "author_id":      t.get("author_id", ""),
                "content":        t.get("text", ""),
                "url":            f"https://x.com/i/web/status/{t['id']}",
                "likes":          metrics.get("like_count", 0),
                "comments":       metrics.get("reply_count", 0),
                "shares":         metrics.get("retweet_count", 0),
                "views":          metrics.get("impression_count", 0),
                "hashtags":       hashtags,
                "search_term":    term,
                "language":       t.get("lang", ""),
                "posted_at":      t.get("created_at"),
                "team_mentioned": detect_team(t.get("text", "")),
            }
            all_posts.append(post)

        # Respeita rate limit entre termos
        time.sleep(2)

    log.info(f"Total de posts capturados: {len(all_posts)}")
    return all_posts


# ── Detectar seleção ──────────────────────────────────────────
def detect_team(text: str) -> Optional[str]:
    if not text:
        return None
    text_lower = text.lower()
    for team, keywords in TEAM_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return team
    return None


# ── Análise de sentimento ─────────────────────────────────────
def call_ai(prompt: str) -> str:
    if GROQ_KEY:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"]
        log.error(f"Erro Groq: {resp.status_code}")

    if GROK_KEY:
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROK_KEY}", "Content-Type": "application/json"},
            json={"model": "grok-3-mini", "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"]

    return "[]"


def analyze_sentiment_batch(posts: list) -> list:
    """Analisa sentimento de até 10 posts por vez."""
    results = []
    batch_size = 10

    for i in range(0, len(posts), batch_size):
        batch = posts[i:i + batch_size]
        texts = "\n---\n".join([
            f"POST {j+1}: {p['content'][:250]}"
            for j, p in enumerate(batch)
        ])

        prompt = f"""Analise o sentimento dos posts abaixo sobre Copa do Mundo 2026.

{texts}

Retorne APENAS um JSON válido (sem texto adicional, sem markdown):
[
  {{
    "index": 1,
    "sentiment": "positive|negative|neutral",
    "score": 0.0,
    "emotion": "joy|anger|fear|surprise|sadness|neutral",
    "is_prediction": true|false,
    "predicted_winner": "nome do time ou null"
  }}
]

score: -1.0 (muito negativo) a 1.0 (muito positivo).
is_prediction: true se o post prevê resultado de jogo.
predicted_winner: time que o post aponta como vencedor (ou null)."""

        try:
            resp_text = call_ai(prompt)
            json_match = re.search(r'\[.*?\]', resp_text, re.DOTALL)
            if json_match:
                batch_results = json.loads(json_match.group())
                for r in batch_results:
                    idx = r.get("index", 1) - 1
                    if 0 <= idx < len(batch):
                        results.append({
                            "post_id":          batch[idx]["post_id"],
                            "sentiment":        r.get("sentiment", "neutral"),
                            "score":            float(r.get("score", 0)),
                            "emotion":          r.get("emotion", "neutral"),
                            "team_mentioned":   batch[idx].get("team_mentioned"),
                            "is_prediction":    r.get("is_prediction", False),
                            "predicted_winner": r.get("predicted_winner"),
                        })
        except Exception as e:
            log.error(f"Erro análise batch {i}: {e}")

        time.sleep(1)

    log.info(f"Sentimento analisado: {len(results)} posts")
    return results


# ── Salvar no Supabase ────────────────────────────────────────
def save_posts(sb, posts: list) -> int:
    if not posts:
        return 0
    unique = list({p["post_id"]: p for p in posts if p.get("post_id")}.values())
    sb.table("social_posts").upsert(unique, on_conflict="post_id").execute()
    log.info(f"Posts salvos: {len(unique)}")
    return len(unique)


def save_sentiment(sb, analyses: list) -> int:
    if not analyses:
        return 0
    sb.table("sentiment_analysis").upsert(analyses, on_conflict="post_id").execute()
    log.info(f"Análises salvas: {len(analyses)}")
    return len(analyses)


def update_team_sentiment(sb):
    from collections import defaultdict
    result = sb.table("sentiment_analysis").select(
        "team_mentioned, sentiment, score"
    ).not_.is_("team_mentioned", "null").execute()

    if not result.data:
        return

    teams = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "scores": []})
    for row in result.data:
        team = row["team_mentioned"]
        teams[team][row["sentiment"]] += 1
        if row["score"] is not None:
            teams[team]["scores"].append(float(row["score"]))

    TEAM_TLA = {t[0]: t[1] for t in [
        ("Brazil","BRA"),("Argentina","ARG"),("France","FRA"),
        ("England","ENG"),("Germany","GER"),("Spain","ESP"),
        ("Portugal","POR"),("Netherlands","NED"),("Uruguay","URU"),
    ]}

    for team_name, data in teams.items():
        total = data["positive"] + data["negative"] + data["neutral"]
        avg   = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        sb.table("team_sentiment").upsert({
            "team_name":      team_name,
            "team_tla":       TEAM_TLA.get(team_name),
            "platform":       "twitter",
            "positive_count": data["positive"],
            "negative_count": data["negative"],
            "neutral_count":  data["neutral"],
            "total_posts":    total,
            "avg_score":      round(avg, 3),
            "period":         "general",
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        }, on_conflict="team_name, platform, period, match_id").execute()

    log.info(f"Sentimento por time atualizado: {len(teams)} times")


# ── Main ──────────────────────────────────────────────────────
def run(analyze_only: bool = False):
    sb = get_supabase()
    log.info("Conexão Supabase OK")

    all_posts = []

    if not analyze_only:
        all_posts = capture_twitter(SEARCH_TERMS, max_per_term=30)
        if all_posts:
            save_posts(sb, all_posts)

    # Analisa posts ainda sem sentimento
    posts_to_analyze = all_posts[:50] if all_posts else []

    if not posts_to_analyze:
        recent = sb.table("social_posts").select(
            "post_id, content, team_mentioned"
        ).limit(50).execute()
        posts_to_analyze = recent.data or []

    if posts_to_analyze:
        analyses = analyze_sentiment_batch(posts_to_analyze)
        save_sentiment(sb, analyses)
        update_team_sentiment(sb)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    run(analyze_only=args.analyze)
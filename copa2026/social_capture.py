"""
Copa 2026 AI — Captura X/Twitter via GetXAPI
$0.001 por chamada (~20 tweets) — $0.10 grátis no cadastro
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
from datetime import datetime, timezone
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
GETXAPI_KEY  = os.getenv("GETXAPI_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GROQ_KEY     = os.getenv("GROQ_API_KEY")
GROK_KEY     = os.getenv("GROK_API_KEY")

GETXAPI_BASE = "https://api.getxapi.com"

# Termos de busca
SEARCH_TERMS = [
    "#Copa2026",
    "#WorldCup2026",
    "#WorldCup",
    "Copa do Mundo 2026",
    "#FIFAWorldCup",
]

# Palavras-chave por seleção
TEAM_KEYWORDS = {
    "Brazil":      ["brazil", "brasil", "seleção", "canarinho", "#bra", "brasileira", "vinicius", "endrick"],
    "Argentina":   ["argentina", "albiceleste", "#arg", "messi", "scaloni"],
    "France":      ["france", "frança", "les bleus", "#fra", "mbappé", "mbappe"],
    "England":     ["england", "inglaterra", "three lions", "#eng", "kane"],
    "Germany":     ["germany", "alemanha", "mannschaft", "#ger"],
    "Spain":       ["spain", "espanha", "la roja", "#esp"],
    "Portugal":    ["portugal", "#por", "ronaldo", "cr7"],
    "Netherlands": ["netherlands", "holanda", "oranje", "#ned"],
    "Uruguay":     ["uruguay", "uruguai", "celeste", "#uru"],
}


# ── Supabase ──────────────────────────────────────────────────
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── GetXAPI ───────────────────────────────────────────────────
def getxapi_search(query: str, max_pages: int = 2) -> list:
    """
    Busca tweets via GetXAPI advanced_search.
    $0.001 por chamada, ~20 tweets por página.
    """
    headers = {"Authorization": f"Bearer {GETXAPI_KEY}"}
    tweets  = []
    cursor  = None

    for page in range(max_pages):
        params = {
            "q":       f"{query} -filter:retweets",
            "product": "Latest",
        }
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            f"{GETXAPI_BASE}/twitter/tweet/advanced_search",
            headers=headers,
            params=params,
            timeout=20,
        )

        if not resp.ok:
            log.error(f"Erro GetXAPI: {resp.status_code} — {resp.text[:200]}")
            break

        data     = resp.json()
        batch    = data.get("tweets", [])
        tweets.extend(batch)
        log.info(f"Página {page+1} — {len(batch)} tweets para '{query[:40]}'")

        if not data.get("has_more") or not batch:
            break

        cursor = data.get("next_cursor")
        time.sleep(0.5)

    return tweets


def capture_twitter(search_terms: list, pages_per_term: int = 2) -> list:
    if not GETXAPI_KEY:
        raise ValueError("GETXAPI_KEY não definido no .env")

    all_posts = []

    for term in search_terms:
        tweets = getxapi_search(term, max_pages=pages_per_term)

        for t in tweets:
            author   = t.get("author", {})
            hashtags = [h.lower() for h in t.get("hashtags", [])]

            post = {
                "platform":       "twitter",
                "post_id":        str(t.get("id", t.get("tweet_id", ""))),
                "author":         author.get("userName", author.get("name", "")),
                "author_id":      str(author.get("id", "")),
                "content":        t.get("text", t.get("full_text", "")),
                "url":            t.get("url", f"https://x.com/i/web/status/{t.get('id','')}"),
                "likes":          t.get("likeCount", t.get("favorite_count", 0)) or 0,
                "comments":       t.get("replyCount", t.get("reply_count", 0)) or 0,
                "shares":         t.get("retweetCount", t.get("retweet_count", 0)) or 0,
                "views":          t.get("viewCount", t.get("views", 0)) or 0,
                "hashtags":       hashtags,
                "search_term":    term,
                "language":       t.get("lang", ""),
                "posted_at":      t.get("createdAt", t.get("created_at")),
                "team_mentioned": detect_team(t.get("text", t.get("full_text", ""))),
            }

            if post["post_id"]:
                all_posts.append(post)

        time.sleep(1)

    log.info(f"Total capturado: {len(all_posts)} posts")
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


# ── IA: análise de sentimento ─────────────────────────────────
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
    results    = []
    batch_size = 10

    for i in range(0, len(posts), batch_size):
        batch = posts[i:i + batch_size]
        texts = "\n---\n".join([
            f"POST {j+1}: {p['content'][:250]}"
            for j, p in enumerate(batch)
        ])

        prompt = f"""Analise o sentimento dos posts abaixo sobre Copa do Mundo 2026.

{texts}

Retorne APENAS JSON válido (sem texto extra, sem markdown):
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
predicted_winner: time apontado como vencedor (ou null)."""

        try:
            resp_text   = call_ai(prompt)
            json_match  = re.search(r'\[.*?\]', resp_text, re.DOTALL)
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


# ── Supabase: salvar ──────────────────────────────────────────
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

    TEAM_TLA = {
        "Brazil": "BRA", "Argentina": "ARG", "France": "FRA",
        "England": "ENG", "Germany": "GER", "Spain": "ESP",
        "Portugal": "POR", "Netherlands": "NED", "Uruguay": "URU",
    }

    teams = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "scores": []})
    for row in result.data:
        team = row["team_mentioned"]
        teams[team][row["sentiment"]] += 1
        if row["score"] is not None:
            teams[team]["scores"].append(float(row["score"]))

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
        all_posts = capture_twitter(SEARCH_TERMS, pages_per_term=2)
        if all_posts:
            save_posts(sb, all_posts)

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
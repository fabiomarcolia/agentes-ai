"""
Copa 2026 AI — Captura de Redes Sociais via Apify
Plataformas: X/Twitter + Instagram
Destino: Supabase

Uso:
    python social_capture.py                    # captura tudo
    python social_capture.py --platform twitter # só Twitter
    python social_capture.py --platform instagram
    python social_capture.py --analyze          # só roda análise de sentimento
"""

import os
import re
import time
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
APIFY_TOKEN  = os.getenv("APIFY_API_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GROQ_KEY     = os.getenv("GROQ_API_KEY")
GROK_KEY     = os.getenv("GROK_API_KEY")

APIFY_BASE   = "https://api.apify.com/v2"

# Termos de busca
SEARCH_TERMS = [
    "#Copa2026",
    "#WorldCup2026",
    "#WorldCup",
    "#FIFAWorldCup",
    "Copa do Mundo 2026",
]

# Seleções para monitorar sentimento
TEAMS = [
    ("Brazil", "BRA"), ("Argentina", "ARG"), ("France", "FRA"),
    ("England", "ENG"), ("Germany", "GER"), ("Spain", "ESP"),
    ("Portugal", "POR"), ("Netherlands", "NED"), ("Uruguay", "URU"),
]


# ── Supabase ──────────────────────────────────────────────────
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Apify helpers ─────────────────────────────────────────────
def apify_run(actor_id: str, input_data: dict, timeout: int = 120) -> list:
    """Roda um actor Apify e retorna os resultados."""
    headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}

    # Inicia a execução
    log.info(f"Iniciando Apify actor: {actor_id}")
    resp = requests.post(
        f"{APIFY_BASE}/acts/{actor_id}/runs?timeout={timeout}&memory=256",
        headers=headers,
        json=input_data,
        timeout=30,
    )
    resp.raise_for_status()
    run_id = resp.json()["data"]["id"]
    log.info(f"Run iniciado: {run_id}")

    # Aguarda conclusão
    for _ in range(timeout // 5):
        time.sleep(5)
        status_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            headers=headers,
            timeout=15,
        )
        status = status_resp.json()["data"]["status"]
        log.info(f"Status: {status}")
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            log.error(f"Run falhou com status: {status}")
            return []

    # Busca resultados
    dataset_id = status_resp.json()["data"]["defaultDatasetId"]
    items_resp  = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items?clean=true&limit=100",
        headers=headers,
        timeout=30,
    )
    items = items_resp.json()
    log.info(f"Itens retornados: {len(items)}")
    return items


# ── Captura Twitter/X ─────────────────────────────────────────
def capture_twitter(search_terms: list, max_items: int = 50) -> list:
    """
    Usa o actor: apidojo/tweet-scraper
    Documentação: https://apify.com/apidojo/tweet-scraper
    """
    posts = []

    for term in search_terms:
        log.info(f"Capturando Twitter: {term}")
        items = apify_run(
            actor_id="apidojo/tweet-scraper",
            input_data={
                "searchTerms":   [term],
                "maxItems":      max_items,
                "lang":          "",          # qualquer idioma
                "sort":          "Latest",
                "tweetLanguage": "",
            },
        )

        for item in items:
            try:
                post = {
                    "platform":    "twitter",
                    "post_id":     str(item.get("id", item.get("tweetId", ""))),
                    "author":      item.get("author", {}).get("userName") or item.get("user", {}).get("screen_name"),
                    "author_id":   str(item.get("author", {}).get("id", "")),
                    "content":     item.get("text", item.get("full_text", "")),
                    "url":         item.get("url", ""),
                    "likes":       item.get("likeCount", item.get("favorite_count", 0)) or 0,
                    "comments":    item.get("replyCount", item.get("reply_count", 0)) or 0,
                    "shares":      item.get("retweetCount", item.get("retweet_count", 0)) or 0,
                    "views":       item.get("viewCount", 0) or 0,
                    "hashtags":    [h.lower() for h in (item.get("hashtags") or [])],
                    "search_term": term,
                    "language":    item.get("lang", ""),
                    "posted_at":   item.get("createdAt", item.get("created_at")),
                }

                # Identifica seleção mencionada
                post["team_mentioned"] = detect_team(post["content"])

                if post["post_id"]:
                    posts.append(post)
            except Exception as e:
                log.warning(f"Erro ao processar tweet: {e}")

    return posts


# ── Captura Instagram ─────────────────────────────────────────
def capture_instagram(search_terms: list, max_items: int = 30) -> list:
    """
    Usa o actor: apify/instagram-hashtag-scraper
    Documentação: https://apify.com/apify/instagram-hashtag-scraper
    """
    posts = []

    # Converte termos para hashtags do Instagram
    hashtags = []
    for term in search_terms:
        tag = term.replace("#", "").replace(" ", "").lower()
        hashtags.append(tag)

    # Adiciona hashtags específicas do Instagram
    hashtags += ["copa2026", "worldcup2026", "fifaworldcup2026", "worldcup"]
    hashtags = list(set(hashtags))  # remove duplicatas

    log.info(f"Capturando Instagram hashtags: {hashtags}")
    items = apify_run(
        actor_id="apify/instagram-hashtag-scraper",
        input_data={
            "hashtags":  hashtags,
            "resultsLimit": max_items,
        },
    )

    for item in items:
        try:
            post = {
                "platform":    "instagram",
                "post_id":     str(item.get("id", item.get("shortCode", ""))),
                "author":      item.get("ownerUsername", ""),
                "author_id":   str(item.get("ownerId", "")),
                "content":     item.get("caption", "") or "",
                "url":         item.get("url", f"https://instagram.com/p/{item.get('shortCode', '')}"),
                "likes":       item.get("likesCount", 0) or 0,
                "comments":    item.get("commentsCount", 0) or 0,
                "shares":      0,
                "views":       item.get("videoViewCount", 0) or 0,
                "hashtags":    [h.lower() for h in (item.get("hashtags") or [])],
                "search_term": "instagram_hashtag",
                "language":    "",
                "posted_at":   item.get("timestamp"),
            }

            post["team_mentioned"] = detect_team(post["content"])

            if post["post_id"]:
                posts.append(post)
        except Exception as e:
            log.warning(f"Erro ao processar post Instagram: {e}")

    return posts


# ── Detectar seleção ──────────────────────────────────────────
def detect_team(text: str) -> Optional[str]:
    """Detecta qual seleção é mencionada no texto."""
    if not text:
        return None

    text_lower = text.lower()
    team_keywords = {
        "Brazil":      ["brazil", "brasil", "seleção", "canarinho", "#bra", "brasileira"],
        "Argentina":   ["argentina", "albiceleste", "#arg", "messi"],
        "France":      ["france", "frança", "les bleus", "#fra", "mbappé", "mbappe"],
        "England":     ["england", "inglaterra", "three lions", "#eng"],
        "Germany":     ["germany", "alemanha", "mannschaft", "#ger", "#deu"],
        "Spain":       ["spain", "espanha", "la roja", "#esp"],
        "Portugal":    ["portugal", "#por", "ronaldo", "cr7"],
        "Netherlands": ["netherlands", "holanda", "oranje", "#ned", "#nld"],
        "Uruguay":     ["uruguay", "uruguai", "celeste", "#uru"],
    }

    for team, keywords in team_keywords.items():
        if any(kw in text_lower for kw in keywords):
            return team

    return None


# ── Análise de sentimento ─────────────────────────────────────
def analyze_sentiment_batch(posts: list) -> list:
    """Analisa sentimento de posts em lote via Groq."""
    if not posts:
        return []

    # Processa em lotes de 10
    results = []
    batch_size = 10

    for i in range(0, len(posts), batch_size):
        batch = posts[i:i + batch_size]
        texts = "\n---\n".join([
            f"POST {j+1}: {p['content'][:200]}"
            for j, p in enumerate(batch)
        ])

        prompt = f"""Analise o sentimento dos posts abaixo sobre Copa do Mundo 2026.

{texts}

Para cada post retorne APENAS um JSON válido no formato:
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

score deve ser entre -1.0 (muito negativo) e 1.0 (muito positivo).
is_prediction = true se o post faz previsão de resultado.
Retorne APENAS o JSON, sem texto adicional."""

        try:
            resp = call_ai(prompt)
            # Extrai JSON da resposta
            json_match = re.search(r'\[.*?\]', resp, re.DOTALL)
            if json_match:
                import json
                batch_results = json.loads(json_match.group())
                for r in batch_results:
                    idx = r.get("index", 1) - 1
                    if 0 <= idx < len(batch):
                        results.append({
                            "post_id":         batch[idx]["post_id"],
                            "sentiment":       r.get("sentiment", "neutral"),
                            "score":           float(r.get("score", 0)),
                            "emotion":         r.get("emotion", "neutral"),
                            "team_mentioned":  batch[idx].get("team_mentioned"),
                            "is_prediction":   r.get("is_prediction", False),
                            "predicted_winner": r.get("predicted_winner"),
                        })
        except Exception as e:
            log.error(f"Erro na análise de sentimento batch {i}: {e}")

        time.sleep(1)  # rate limit

    log.info(f"Sentimento analisado: {len(results)} posts")
    return results


def call_ai(prompt: str) -> str:
    """Chama IA para análise — Groq preferencial."""
    if GROQ_KEY:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"]

    if GROK_KEY:
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROK_KEY}", "Content-Type": "application/json"},
            json={"model": "grok-3-mini", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"]

    return "[]"


# ── Salvar no Supabase ────────────────────────────────────────
def save_posts(sb, posts: list) -> int:
    if not posts:
        return 0

    # Remove duplicatas pelo post_id
    unique = {p["post_id"]: p for p in posts if p.get("post_id")}
    to_insert = list(unique.values())

    result = sb.table("social_posts").upsert(
        to_insert, on_conflict="post_id"
    ).execute()

    log.info(f"Posts salvos: {len(to_insert)}")
    return len(to_insert)


def save_sentiment(sb, analyses: list) -> int:
    if not analyses:
        return 0

    sb.table("sentiment_analysis").upsert(
        analyses, on_conflict="post_id"
    ).execute()

    log.info(f"Análises salvas: {len(analyses)}")
    return len(analyses)


def update_team_sentiment(sb):
    """Agrega sentimento por seleção e atualiza a tabela."""
    result = sb.table("sentiment_analysis").select(
        "team_mentioned, sentiment, score"
    ).not_.is_("team_mentioned", "null").execute()

    if not result.data:
        return

    # Agrega por time
    from collections import defaultdict
    teams = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "scores": []})

    for row in result.data:
        team = row["team_mentioned"]
        sent = row["sentiment"]
        teams[team][sent] += 1
        if row["score"] is not None:
            teams[team]["scores"].append(float(row["score"]))

    for team_name, data in teams.items():
        total = data["positive"] + data["negative"] + data["neutral"]
        avg   = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0

        # Busca TLA
        tla = next((t[1] for t in TEAMS if t[0].lower() == team_name.lower()), None)

        sb.table("team_sentiment").upsert({
            "team_name":      team_name,
            "team_tla":       tla,
            "platform":       "all",
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
def run(platform: Optional[str] = None, analyze_only: bool = False):
    if not APIFY_TOKEN:
        raise ValueError("APIFY_API_TOKEN não definido no .env")

    sb = get_supabase()
    log.info("Conexão com Supabase OK")

    all_posts = []

    if not analyze_only:
        if platform in (None, "twitter"):
            twitter_posts = capture_twitter(SEARCH_TERMS, max_items=50)
            all_posts.extend(twitter_posts)

        if platform in (None, "instagram"):
            instagram_posts = capture_instagram(SEARCH_TERMS, max_items=30)
            all_posts.extend(instagram_posts)

        if all_posts:
            save_posts(sb, all_posts)

    # Análise de sentimento dos posts salvos
    log.info("Iniciando análise de sentimento...")
    recent = (
        sb.table("social_posts")
        .select("post_id, content, team_mentioned")
        .is_("sentiment_analysis.post_id", "null")
        .limit(100)
        .execute()
    )

    posts_to_analyze = recent.data or all_posts[:50]
    if posts_to_analyze:
        analyses = analyze_sentiment_batch(posts_to_analyze)
        save_sentiment(sb, analyses)
        update_team_sentiment(sb)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Captura redes sociais Copa 2026")
    parser.add_argument("--platform", type=str, help="twitter | instagram")
    parser.add_argument("--analyze", action="store_true", help="Só análise de sentimento")
    args = parser.parse_args()

    run(platform=args.platform, analyze_only=args.analyze)

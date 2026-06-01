"""
Copa 2026 AI — Postagem automática no canal
Rodado pelo GitHub Actions:
  - Todo dia às 8h BRT: jogos do dia
  - Após cada rodada: resumo do jogo
  - Todo dia às 9h BRT: curiosidade do dia
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

import requests
from supabase import create_client

load_dotenv(override=False)

BRT = timezone(timedelta(hours=-3))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("copa2026-broadcast")


def send(text: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    channel = os.getenv("TELEGRAM_CHANNEL_ID", "-1001003879622443")

    log.info(f"Enviando para canal: {channel}")
    log.info(f"Token presente: {'sim' if token else 'NAO — variavel vazia!'}")

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": channel, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    if resp.ok:
        log.info("Mensagem enviada com sucesso!")
    else:
        log.error(f"Erro ao enviar: {resp.status_code} — {resp.text}")


def gerar_texto_ia(prompt: str, modo: str = "resumo") -> str:
    """
    Ordem de prioridade:
    Claude  → análises longas
    Groq    → resumos e curiosidades (free tier generoso)
    Grok    → se tiver crédito
    Gemini  → fallback
    """

    log.info(f"GROK_API_KEY presente: {'sim' if os.getenv('GROK_API_KEY') else 'NAO'}")
    log.info(f"GEMINI_API_KEY presente: {'sim' if os.getenv('GEMINI_API_KEY') else 'NAO'}")
    log.info(f"GROQ_API_KEY presente: {'sim' if os.getenv('GROQ_API_KEY') else 'NAO'}")

    # Claude — análises longas
    if os.getenv("ANTHROPIC_API_KEY") and modo == "analise":
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         os.getenv("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"]

    # Groq — free tier generoso (14.400 req/dia), ultra-rápido
    if os.getenv("GROQ_API_KEY"):
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"]
        else:
            log.error(f"Erro Groq: {resp.status_code} — {resp.text}")

    # Grok — quando tiver crédito
    if os.getenv("GROK_API_KEY"):
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('GROK_API_KEY')}", "Content-Type": "application/json"},
            json={"model": "grok-3-mini", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"]
        else:
            log.error(f"Erro Grok: {resp.status_code} — {resp.text}")

    # Gemini — fallback
    if os.getenv("GEMINI_API_KEY"):
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={os.getenv('GEMINI_API_KEY')}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            log.error(f"Erro Gemini: {resp.status_code} — {resp.text}")

    # Claude como último recurso
    if os.getenv("ANTHROPIC_API_KEY"):
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         os.getenv("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"]

    return "IA indisponivel no momento."



def broadcast_jogos_do_dia():
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    hoje = datetime.now(BRT).date()
    inicio = datetime(hoje.year, hoje.month, hoje.day, 0, 0, tzinfo=BRT).isoformat()
    fim    = datetime(hoje.year, hoje.month, hoje.day, 23, 59, tzinfo=BRT).isoformat()

    result = (
        sb.table("matches")
        .select("home_team_name, away_team_name, utc_date, stage, group_name")
        .gte("utc_date", inicio)
        .lte("utc_date", fim)
        .order("utc_date")
        .execute()
    )

    jogos = result.data
    if not jogos:
        log.info("Nenhum jogo hoje — não postando.")
        return

    linhas = [f"⚽ *Jogos de hoje — {hoje.strftime('%d/%m')}*\n"]
    for j in jogos:
        hora   = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%H:%M")
        grupo  = f"Grupo {j['group_name']}" if j.get("group_name") else j.get("stage", "")
        linhas.append(f"🕐 {hora}h — *{j['home_team_name']}* x *{j['away_team_name']}* — _{grupo}_")

    linhas.append("\n💬 Use @copa2026ai\\_bot para stats e resumos!")
    send("\n".join(linhas))
    log.info(f"Jogos do dia postados: {len(jogos)} jogos")


def broadcast_curiosidade():
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    total_jogos = sb.table("matches").select("id", count="exact").eq("status", "FINISHED").execute()
    total_gols  = sb.table("goals").select("id", count="exact").execute()

    jogos_count = total_jogos.count or 0
    gols_count  = total_gols.count or 0
    media = round(gols_count / jogos_count, 2) if jogos_count > 0 else 0

    prompt = f"""Você é um analista de dados esportivos especializado em Copa do Mundo.
Dados atuais da Copa 2026: {jogos_count} jogos, {gols_count} gols, média de {media} gols/jogo.

Gere UMA curiosidade interessante comparando com Copas anteriores ou destacando um padrão inusitado.

Regras de formatação:
- Máximo 150 palavras
- Em português
- Divida em 2 ou 3 parágrafos curtos separados por linha em branco
- Sem markdown, sem asteriscos, sem bullets
- Comece direto com a curiosidade, sem introdução"""

    curiosidade = gerar_texto_ia(prompt, modo="resumo")
    send(f"💡 *Curiosidade do dia*\n\n{curiosidade}\n\n🤖 _Gerado por IA · Copa 2026 AI_")
    log.info("Curiosidade postada")


def broadcast_resumo_ultimo_jogo():
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

    result = (
        sb.table("matches")
        .select("*")
        .eq("status", "FINISHED")
        .order("utc_date", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        log.info("Nenhum jogo finalizado para resumir.")
        return

    j = result.data[0]

    gols_result = (
        sb.table("goals")
        .select("scorer_name, team_name, minute, type")
        .eq("match_id", j["external_id"])
        .order("minute")
        .execute()
    )
    gols = gols_result.data or []
    gols_str = "\n".join([f"  ⚽ {g['scorer_name']} ({g['team_name']}) {g['minute']}'" for g in gols]) or "  Sem dados"

    prompt = f"""Você é um narrador esportivo brasileiro animado.
Resuma em até 200 palavras o jogo da Copa 2026:
{j['home_team_name']} {j['home_score']} x {j['away_score']} {j['away_team_name']}
Intervalo: {j['home_score_ht']} x {j['away_score_ht']}
Gols: {gols_str}
Em português, com entusiasmo, sem markdown."""

    resumo = gerar_texto_ia(prompt, modo="resumo")
    data   = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%d/%m")

    send(
        f"📝 *Resumo — {j['home_team_name']} {j['home_score']}x{j['away_score']} {j['away_team_name']}*\n"
        f"_{data} · {j.get('stage', 'Copa 2026')}_\n\n"
        f"{resumo}\n\n"
        f"🤖 _Gerado por IA · Copa 2026 AI_"
    )
    log.info("Resumo pós-jogo postado")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "jogos"

    if mode == "jogos":
        broadcast_jogos_do_dia()
    elif mode == "curiosidade":
        broadcast_curiosidade()
    elif mode == "resumo":
        broadcast_resumo_ultimo_jogo()
    else:
        log.error(f"Modo inválido: {mode}. Use: jogos | curiosidade | resumo")
"""
Copa 2026 AI — Bot Telegram Interativo
Comandos disponíveis:
    /start        → boas-vindas
    /jogos        → jogos de hoje
    /artilharia   → top 10 artilheiros
    /grupo <A-L>  → tabela do grupo
    /brasil       → próximo jogo do Brasil
    /resumo       → resumo do último jogo finalizado (IA)
    /curiosidade  → curiosidade gerada por IA
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

import requests
from supabase import create_client, Client

load_dotenv(override=False)

# ── Config ────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID       = os.getenv("TELEGRAM_CHANNEL_ID", "-1001003879622443")
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY")
GEMINI_KEY       = os.getenv("GEMINI_API_KEY")
SUPABASE_URL     = os.getenv("SUPABASE_URL")
SUPABASE_KEY     = os.getenv("SUPABASE_SERVICE_KEY")

TELEGRAM_API     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
BRT              = timezone(timedelta(hours=-3))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("copa2026-bot")


# ── Supabase ──────────────────────────────────────────────────
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Telegram helpers ──────────────────────────────────────────
def send_message(chat_id: str, text: str, parse_mode: str = "Markdown"):
    resp = requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": parse_mode,
    }, timeout=15)
    if not resp.ok:
        log.error(f"Erro ao enviar mensagem: {resp.text}")
    return resp.json()


def get_updates(offset: int = 0):
    resp = requests.get(f"{TELEGRAM_API}/getUpdates", params={
        "offset":  offset,
        "timeout": 30,
    }, timeout=35)
    return resp.json().get("result", [])


# ── IA: gerar texto ───────────────────────────────────────────
def gerar_texto_ia(prompt: str) -> str:
    """Tenta Claude primeiro, cai no Gemini se não tiver chave."""

    # Claude
    if ANTHROPIC_KEY:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"]

    # Gemini (fallback gratuito)
    if GEMINI_KEY:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        if resp.ok:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    return "⚠️ Serviço de IA temporariamente indisponível."


# ── Comandos ──────────────────────────────────────────────────

def cmd_start(chat_id: str):
    msg = (
        "⚽ *Copa 2026 AI*\n\n"
        "Olá! Sou um agente de IA acompanhando a Copa do Mundo 2026 em tempo real.\n\n"
        "*Comandos disponíveis:*\n"
        "/jogos — Jogos de hoje\n"
        "/artilharia — Top 10 artilheiros\n"
        "/grupo A — Tabela do Grupo A (troque a letra)\n"
        "/brasil — Próximo jogo do Brasil\n"
        "/resumo — Resumo do último jogo (IA)\n"
        "/curiosidade — Curiosidade gerada por IA\n\n"
        "📢 Acompanhe o canal: @Copa2026AI"
    )
    send_message(chat_id, msg)


def cmd_jogos(chat_id: str):
    sb = get_supabase()
    hoje_brt = datetime.now(BRT).date()
    inicio   = datetime(hoje_brt.year, hoje_brt.month, hoje_brt.day, 0, 0, tzinfo=BRT).isoformat()
    fim      = datetime(hoje_brt.year, hoje_brt.month, hoje_brt.day, 23, 59, tzinfo=BRT).isoformat()

    result = (
        sb.table("matches")
        .select("home_team_name, away_team_name, utc_date, status, home_score, away_score, stage, group_name")
        .gte("utc_date", inicio)
        .lte("utc_date", fim)
        .order("utc_date")
        .execute()
    )

    jogos = result.data
    if not jogos:
        send_message(chat_id, "📅 Nenhum jogo hoje.")
        return

    linhas = [f"⚽ *Jogos de hoje — {hoje_brt.strftime('%d/%m')}*\n"]
    for j in jogos:
        hora = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%H:%M")
        grupo = f"Grupo {j['group_name']}" if j.get("group_name") else j.get("stage", "")

        if j["status"] == "FINISHED":
            placar = f"{j['home_score']} x {j['away_score']}"
            linhas.append(f"✅ *{j['home_team_name']}* {placar} *{j['away_team_name']}* — {grupo}")
        elif j["status"] == "IN_PLAY":
            linhas.append(f"🔴 *{j['home_team_name']}* x *{j['away_team_name']}* — AO VIVO")
        else:
            linhas.append(f"🕐 {hora}h — *{j['home_team_name']}* x *{j['away_team_name']}* — {grupo}")

    send_message(chat_id, "\n".join(linhas))


def cmd_artilharia(chat_id: str):
    sb = get_supabase()
    result = (
        sb.table("top_scorers")
        .select("player_name, team_name, goals, assists, penalties")
        .order("goals", desc=True)
        .limit(10)
        .execute()
    )

    scorers = result.data
    if not scorers:
        send_message(chat_id, "📊 Artilharia ainda não disponível — a Copa começa dia 11/06!")
        return

    linhas = ["🥇 *Artilharia — Copa 2026*\n"]
    medals = ["🥇", "🥈", "🥉"] + ["▪️"] * 7
    for i, s in enumerate(scorers):
        assists = f" | {s['assists']} ass." if s.get("assists") else ""
        linhas.append(f"{medals[i]} {s['player_name']} ({s['team_name']}) — *{s['goals']} gols*{assists}")

    send_message(chat_id, "\n".join(linhas))


def cmd_grupo(chat_id: str, letra: str):
    sb = get_supabase()
    letra = letra.upper().strip()

    result = (
        sb.table("matches")
        .select("home_team_name, away_team_name, home_score, away_score, status, utc_date")
        .eq("group_name", letra)
        .order("utc_date")
        .execute()
    )

    jogos = result.data
    if not jogos:
        send_message(chat_id, f"❌ Grupo {letra} não encontrado. Use /grupo A até /grupo L")
        return

    linhas = [f"📊 *Grupo {letra}*\n"]
    for j in jogos:
        if j["status"] == "FINISHED":
            linhas.append(f"✅ {j['home_team_name']} *{j['home_score']} x {j['away_score']}* {j['away_team_name']}")
        else:
            hora = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%d/%m %H:%M")
            linhas.append(f"🕐 {hora}h — {j['home_team_name']} x {j['away_team_name']}")

    send_message(chat_id, "\n".join(linhas))


def cmd_brasil(chat_id: str):
    sb = get_supabase()
    agora = datetime.now(timezone.utc).isoformat()

    result = (
        sb.table("matches")
        .select("home_team_name, away_team_name, utc_date, status, home_score, away_score, stage, group_name")
        .or_("home_team_name.ilike.%brasil%,away_team_name.ilike.%brasil%,home_team_name.ilike.%brazil%,away_team_name.ilike.%brazil%")
        .gte("utc_date", agora)
        .order("utc_date")
        .limit(1)
        .execute()
    )

    if not result.data:
        # Busca último jogo já jogado
        result = (
            sb.table("matches")
            .select("home_team_name, away_team_name, utc_date, status, home_score, away_score, stage")
            .or_("home_team_name.ilike.%brasil%,away_team_name.ilike.%brasil%,home_team_name.ilike.%brazil%,away_team_name.ilike.%brazil%")
            .eq("status", "FINISHED")
            .order("utc_date", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            send_message(chat_id, "🇧🇷 Nenhum jogo do Brasil encontrado ainda.")
            return

        j = result.data[0]
        send_message(chat_id,
            f"🇧🇷 *Último jogo do Brasil*\n\n"
            f"*{j['home_team_name']}* {j['home_score']} x {j['away_score']} *{j['away_team_name']}*\n"
            f"_{j.get('stage', '')}_"
        )
        return

    j = result.data[0]
    hora = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%d/%m às %H:%M")
    send_message(chat_id,
        f"🇧🇷 *Próximo jogo do Brasil*\n\n"
        f"*{j['home_team_name']}* x *{j['away_team_name']}*\n"
        f"📅 {hora}h (BRT)\n"
        f"_{j.get('stage', '')}_"
    )


def cmd_resumo(chat_id: str):
    sb = get_supabase()

    result = (
        sb.table("matches")
        .select("home_team_name, away_team_name, home_score, away_score, home_score_ht, away_score_ht, utc_date, stage")
        .eq("status", "FINISHED")
        .order("utc_date", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        send_message(chat_id, "⏳ Nenhum jogo finalizado ainda. A Copa começa dia 11/06!")
        return

    j = result.data[0]

    # Busca gols do jogo
    goals_result = (
        sb.table("goals")
        .select("scorer_name, team_name, minute, type")
        .eq("match_id", result.data[0].get("external_id", 0))
        .order("minute")
        .execute()
    )
    gols = goals_result.data or []
    gols_str = "\n".join([f"  ⚽ {g['scorer_name']} ({g['team_name']}) {g['minute']}'" for g in gols]) or "  Sem dados de gols"

    prompt = f"""Você é um narrador esportivo brasileiro animado e inteligente.
Gere um resumo curto e envolvente (máximo 200 palavras) do seguinte jogo da Copa do Mundo 2026:

Jogo: {j['home_team_name']} {j['home_score']} x {j['away_score']} {j['away_team_name']}
Placar no intervalo: {j['home_score_ht']} x {j['away_score_ht']}
Fase: {j.get('stage', 'Copa do Mundo 2026')}
Gols: 
{gols_str}

O resumo deve ser em português, com entusiasmo, destacar o resultado, mencionar os gols e terminar com uma frase de análise sobre o desempenho das equipes.
Não use markdown, escreva em texto corrido."""

    send_message(chat_id, "⏳ Gerando resumo com IA...")
    resumo = gerar_texto_ia(prompt)

    data = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%d/%m")
    msg = (
        f"📝 *Resumo — {j['home_team_name']} {j['home_score']}x{j['away_score']} {j['away_team_name']}*\n"
        f"_{data} · {j.get('stage', 'Copa 2026')}_\n\n"
        f"{resumo}\n\n"
        f"🤖 _Gerado por IA · Copa 2026 AI_"
    )
    send_message(chat_id, msg)


def cmd_curiosidade(chat_id: str):
    sb = get_supabase()

    # Coleta alguns dados para contextualizar a IA
    total_jogos = sb.table("matches").select("id", count="exact").eq("status", "FINISHED").execute()
    total_gols  = sb.table("goals").select("id", count="exact").execute()

    jogos_count = total_jogos.count or 0
    gols_count  = total_gols.count or 0
    media = round(gols_count / jogos_count, 2) if jogos_count > 0 else 0

    prompt = f"""Você é um analista de dados esportivos especializado em Copa do Mundo.
Com base nos seguintes dados da Copa do Mundo 2026 até agora:
- Jogos realizados: {jogos_count}
- Total de gols: {gols_count}
- Média de gols por jogo: {media}

Gere UMA curiosidade interessante, surpreendente ou divertida sobre a Copa do Mundo 2026 ou sobre Copas anteriores para comparação.
Seja criativo, use dados históricos se relevante. Máximo 150 palavras. Em português. Sem markdown."""

    send_message(chat_id, "⏳ Buscando curiosidade...")
    curiosidade = gerar_texto_ia(prompt)

    msg = (
        f"💡 *Curiosidade do dia*\n\n"
        f"{curiosidade}\n\n"
        f"🤖 _Gerado por IA · Copa 2026 AI_"
    )
    send_message(chat_id, msg)


# ── Dispatcher de comandos ────────────────────────────────────
def handle_update(update: dict):
    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    text    = message.get("text", "").strip()

    if not text.startswith("/"):
        return

    parts   = text.split()
    command = parts[0].lower().split("@")[0]  # remove @bot_username se vier

    log.info(f"Comando recebido: {command} de {chat_id}")

    if command == "/start":
        cmd_start(chat_id)
    elif command == "/jogos":
        cmd_jogos(chat_id)
    elif command == "/artilharia":
        cmd_artilharia(chat_id)
    elif command == "/grupo":
        letra = parts[1] if len(parts) > 1 else ""
        if letra:
            cmd_grupo(chat_id, letra)
        else:
            send_message(chat_id, "Use: /grupo A (substitua A pela letra do grupo)")
    elif command == "/brasil":
        cmd_brasil(chat_id)
    elif command == "/resumo":
        cmd_resumo(chat_id)
    elif command == "/curiosidade":
        cmd_curiosidade(chat_id)
    else:
        send_message(chat_id, "❓ Comando não reconhecido. Use /start para ver os comandos disponíveis.")


# ── Loop principal (polling) ──────────────────────────────────
def main():
    log.info("🤖 Copa 2026 AI Bot iniciado")
    offset = 0

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                handle_update(update)
                offset = update["update_id"] + 1
        except KeyboardInterrupt:
            log.info("Bot encerrado.")
            break
        except Exception as e:
            log.error(f"Erro no loop: {e}")


if __name__ == "__main__":
    main()

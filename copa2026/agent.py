"""
Copa 2026 AI — Agente LangGraph
Agente autônomo de cobertura de jogos

Modos:
    python agent.py --mode pos_jogo   # cobertura pós-jogo
    python agent.py --mode pre_jogo   # preview pré-jogo
    python agent.py --mode diario     # resumo diário com contexto
"""

import os
import json
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import TypedDict, Annotated

import requests
from supabase import create_client
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

load_dotenv(override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("copa2026-agent")

BRT = timezone(timedelta(hours=-3))

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_SERVICE_KEY")
GROQ_KEY       = os.getenv("GROQ_API_KEY")
GETXAPI_KEY    = os.getenv("GETXAPI_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHANNEL_ID", "@copa2026ai")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ══════════════════════════════════════════════════════════════
# TOOLS — ferramentas que o agente pode usar
# ══════════════════════════════════════════════════════════════

@tool
def buscar_ultimo_jogo() -> str:
    """Busca o último jogo finalizado no banco de dados."""
    result = (
        sb.table("matches")
        .select("*")
        .eq("status", "FINISHED")
        .order("utc_date", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return "Nenhum jogo finalizado encontrado."

    j = result.data[0]
    data = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%d/%m às %H:%M")
    return json.dumps({
        "match_id":    j["external_id"],
        "home":        j["home_team_name"],
        "away":        j["away_team_name"],
        "placar":      f"{j['home_score']} x {j['away_score']}",
        "intervalo":   f"{j['home_score_ht']} x {j['away_score_ht']}",
        "vencedor":    j.get("winner"),
        "fase":        j.get("stage"),
        "data":        data,
    }, ensure_ascii=False)


@tool
def buscar_proximo_jogo(time: str = "") -> str:
    """Busca o próximo jogo agendado. Opcionalmente filtra por nome do time."""
    agora = datetime.now(timezone.utc).isoformat()
    query = (
        sb.table("matches")
        .select("*")
        .eq("status", "TIMED")
        .gte("utc_date", agora)
        .order("utc_date")
        .limit(1)
    )
    if time:
        query = query.or_(
            f"home_team_name.ilike.%{time}%,away_team_name.ilike.%{time}%"
        )

    result = query.execute()
    if not result.data:
        return "Nenhum jogo agendado encontrado."

    j = result.data[0]
    data = datetime.fromisoformat(j["utc_date"]).astimezone(BRT).strftime("%d/%m às %H:%M")
    return json.dumps({
        "match_id": j["external_id"],
        "home":     j["home_team_name"],
        "away":     j["away_team_name"],
        "data":     data,
        "fase":     j.get("stage"),
        "grupo":    j.get("group_name"),
    }, ensure_ascii=False)


@tool
def buscar_gols_jogo(match_id: int) -> str:
    """Busca os gols de uma partida específica pelo match_id."""
    result = (
        sb.table("goals")
        .select("*")
        .eq("match_id", match_id)
        .order("minute")
        .execute()
    )
    if not result.data:
        return "Nenhum gol registrado para esse jogo ainda."

    gols = []
    for g in result.data:
        tipo = "⚽" if g["type"] == "REGULAR" else ("🥅 (contra)" if g["type"] == "OWN_GOAL" else "⚽ (pênalti)")
        gols.append(f"{g['minute']}' {tipo} {g['scorer_name']} ({g['team_name']})")

    return "\n".join(gols)


@tool
def buscar_sentimento_times(times: str) -> str:
    """
    Busca o sentimento atual no Twitter para uma lista de times.
    Exemplo: times='Brasil,Argentina'
    """
    lista = [t.strip() for t in times.split(",")]
    resultados = []

    for time in lista:
        result = (
            sb.table("team_sentiment")
            .select("*")
            .eq("team_name", time)
            .eq("platform", "twitter")
            .eq("period", "general")
            .limit(1)
            .execute()
        )
        # Se não achar exato, tenta parcial via python
        if not result.data:
            all_result = sb.table("team_sentiment").select("*").execute()
            result_data = [r for r in (all_result.data or []) if time.lower() in r["team_name"].lower()]
            result = type("R", (), {"data": result_data[:1]})()
        if result.data:
            r = result.data[0]
            total = r["total_posts"] or 1
            pct_pos = round(r["positive_count"] / total * 100)
            resultados.append(
                f"{time}: score={r['avg_score']:+.2f} | "
                f"positivo={pct_pos}% | "
                f"posts={r['total_posts']}"
            )
        else:
            resultados.append(f"{time}: sem dados de sentimento ainda")

    return "\n".join(resultados)


@tool
def buscar_tweets_recentes(termo: str, limite: int = 10) -> str:
    """Busca os tweets mais recentes sobre um termo ou time."""
    result = (
        sb.table("social_posts")
        .select("content, likes, shares")
        .ilike("content", f"%{termo}%")
        .order("captured_at", desc=True)
        .limit(limite)
        .execute()
    )

    if not result.data:
        return f"Nenhum tweet encontrado sobre '{termo}'."

    tweets = []
    for p in result.data:
        tweets.append(f"- {p['content'][:120]}... (❤️{p.get('likes', 0)})")

    return "\n".join(tweets)


@tool
def buscar_artilharia(limite: int = 5) -> str:
    """Busca os artilheiros da Copa."""
    result = (
        sb.table("top_scorers")
        .select("*")
        .order("goals", desc=True)
        .limit(limite)
        .execute()
    )

    if not result.data:
        return "Artilharia ainda não disponível."

    linhas = []
    for i, s in enumerate(result.data, 1):
        linhas.append(f"{i}. {s['player_name']} ({s['team_name']}) — {s['goals']} gols")

    return "\n".join(linhas)


@tool
def postar_telegram(text: str) -> str:
    """Posta uma mensagem no canal do Telegram. Use este parâmetro: text (string com a mensagem completa). Chame esta função APENAS UMA VEZ e depois encerre."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat  = os.getenv("TELEGRAM_CHANNEL_ID", "@copa2026ai")

    if not token:
        return "TELEGRAM_BOT_TOKEN não configurado — mensagem não enviada. ENCERRE AGORA."

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id":    chat,
            "text":       text,
            "parse_mode": "Markdown",
        },
        timeout=15,
    )
    if resp.ok:
        log.info("Mensagem postada no Telegram!")
        return "SUCESSO: Mensagem postada no canal. TAREFA CONCLUÍDA. Não chame mais nenhuma ferramenta."
    else:
        log.error(f"Erro Telegram: {resp.text}")
        return f"ERRO ao postar: {resp.status_code}. ENCERRE AGORA sem tentar novamente."


# ══════════════════════════════════════════════════════════════
# ESTADO DO AGENTE
# ══════════════════════════════════════════════════════════════

from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    modo: str
    log_acoes: list[str]


# ══════════════════════════════════════════════════════════════
# GRAFO DO AGENTE
# ══════════════════════════════════════════════════════════════

TOOLS = [
    buscar_ultimo_jogo,
    buscar_proximo_jogo,
    buscar_gols_jogo,
    buscar_sentimento_times,
    buscar_tweets_recentes,
    buscar_artilharia,
    postar_telegram,
]

def criar_agente(modo: str):
    """Cria o agente LangGraph com as ferramentas e modo correto."""

    llm = ChatGroq(
        api_key=GROQ_KEY,
        model="llama-3.3-70b-versatile",
        temperature=0.7,
    ).bind_tools(TOOLS)

    # Prompts por modo
    prompts = {
        "pos_jogo": """Você é um agente de IA cobrindo a Copa do Mundo 2026 em tempo real.
Sua missão: fazer a cobertura completa do último jogo finalizado.

Siga este raciocínio:
1. Busque os dados do último jogo finalizado
2. Busque os gols da partida
3. Busque o sentimento dos times no Twitter
4. Busque tweets recentes sobre o jogo
5. Analise os dados e escolha o ângulo mais interessante (polêmica? virada? goleada? zebra?)
6. Gere um resumo em português, animado, com 3 parágrafos, sem markdown, terminando com análise do sentimento da torcida
7. Poste no canal do Telegram com emojis e formatação Markdown

Seja autônomo — use as ferramentas na ordem que fizer mais sentido.""",

        "pre_jogo": """Você é um agente de IA cobrindo a Copa do Mundo 2026.
Sua missão: fazer o preview do próximo jogo.

Siga este raciocínio:
1. Busque o próximo jogo agendado
2. Busque o sentimento dos dois times no Twitter
3. Busque tweets recentes sobre os times
4. Analise os dados e gere um preview em português com:
   - Contexto do jogo (fase, importância)
   - Análise tática (o que esperar)
   - O que a torcida está falando no Twitter
   - Sua previsão de resultado
5. Poste no canal do Telegram com emojis e formatação Markdown

Seja autônomo — use as ferramentas na ordem que fizer mais sentido.""",

        "diario": """Você é um agente de IA cobrindo a Copa do Mundo 2026.
Sua missão: fazer um resumo diário completo.

Siga este raciocínio:
1. Busque o último jogo finalizado e seus gols
2. Busque o próximo jogo
3. Busque a artilharia atual
4. Busque sentimento geral sobre Brasil e Argentina
5. Com TODOS os dados em mãos, gere UMA ÚNICA mensagem completa em português com:
   ⚽ *Resumo do dia — Copa 2026*
   - Resultado do último jogo (ou que a Copa ainda não começou)
   - Preview do próximo jogo com data e horário
   - Artilharia (ou que ainda não começou)
   - Pulso da torcida nas redes sociais
   🤖 Gerado por IA · Copa 2026 AI
6. Chame postar_telegram UMA ÚNICA VEZ com a mensagem completa

Regras importantes:
- Use quebras de linha entre as seções
- Use emojis para tornar mais visual
- Formate em Markdown do Telegram (*negrito*, _itálico_)
- NUNCA poste mais de uma vez — consolide tudo em um único post
- Seja autônomo e decisivo.""",
    }

    system_prompt = prompts.get(modo, prompts["diario"])

    def agente_node(state: AgentState):
        log.info(f"Agente pensando... (modo: {state['modo']})")
        msgs = [SystemMessage(content=system_prompt)] + state["messages"]
        resposta = llm.invoke(msgs)
        return {"messages": [resposta]}

    def deve_continuar(state: AgentState):
        ultima = state["messages"][-1]

        # Verifica se já postou com sucesso — para o loop
        for msg in reversed(state["messages"]):
            if hasattr(msg, "content") and isinstance(msg.content, str):
                if "TAREFA CONCLUÍDA" in msg.content:
                    log.info("Agente concluiu após postar.")
                    return END

        if hasattr(ultima, "tool_calls") and ultima.tool_calls:
            log.info(f"Agente usando tool: {[t['name'] for t in ultima.tool_calls]}")
            return "tools"
        log.info("Agente concluiu.")
        return END

    tool_node = ToolNode(TOOLS)

    grafo = StateGraph(AgentState)
    grafo.add_node("agente", agente_node)
    grafo.add_node("tools", tool_node)
    grafo.set_entry_point("agente")
    grafo.add_conditional_edges("agente", deve_continuar)
    grafo.add_edge("tools", "agente")

    return grafo.compile(checkpointer=None)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def run(modo: str):
    log.info(f"Iniciando agente Copa 2026 — modo: {modo}")

    agente = criar_agente(modo)

    mensagens_iniciais = {
        "pos_jogo": "Faça a cobertura completa do último jogo da Copa 2026.",
        "pre_jogo": "Faça o preview do próximo jogo da Copa 2026.",
        "diario":   "Faça o resumo diário da Copa 2026 com os últimos resultados e próximos jogos.",
    }

    input_msg = mensagens_iniciais.get(modo, mensagens_iniciais["diario"])

    resultado = agente.invoke(
        {
            "messages":   [HumanMessage(content=input_msg)],
            "modo":       modo,
            "log_acoes":  [],
        },
        config={"recursion_limit": 15}
    )

    ultima_msg = resultado["messages"][-1]
    log.info(f"Agente finalizou. Última mensagem: {ultima_msg.content[:200]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agente Copa 2026")
    parser.add_argument(
        "--mode",
        type=str,
        default="diario",
        choices=["pos_jogo", "pre_jogo", "diario"],
        help="Modo do agente"
    )
    args = parser.parse_args()
    run(modo=args.mode)
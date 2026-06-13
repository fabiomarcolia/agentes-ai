import requests
import json

# Busca jogos de datas anteriores (Copa começou dia 11/06)
dates = ["20260611", "20260612", "20260613"]

for date in dates:
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={date}"
    r = requests.get(url)
    events = r.json().get("events", [])
    
    for event in events:
        comp = event.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {})
        
        if status.get("completed"):
            print(f"\n=== JOGO FINALIZADO: {event['name']} ===")
            print(f"ID ESPN: {event['id']}")
            
            # Placar
            for c in comp.get("competitors", []):
                print(f"  {c['team']['displayName']}: {c['score']}")
            
            # Detalhes (gols)
            details = comp.get("details", [])
            print(f"  Detalhes: {len(details)} eventos")
            if details:
                print(json.dumps(details[:3], indent=2))
            break
    else:
        continue
    break
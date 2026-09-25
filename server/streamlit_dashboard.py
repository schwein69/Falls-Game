import os
import json
import time

import streamlit as st
import redis

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
STATUS_KEY = "matchmaking:status"
REFRESH_SECONDS = 2

st.set_page_config(page_title="Falls Game — Matchmaking Dashboard", layout="wide")


@st.cache_resource
def get_redis_client():
    # @st.cache_resource: la connessione a Redis viene creata una volta sola per l'intera
    # sessione dell'app, non ricreata ad ogni refresh
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def load_status(client):
    """Legge e decodifica lo stato da Redis. None se la chiave non esiste ancora (matchmaking
    server non ancora avviato, o mai arrivato un client) o se Redis non e' raggiungibile."""
    try:
        raw = client.get(STATUS_KEY)
        if raw is None:
            return None, None
        return json.loads(raw), None
    except redis.exceptions.ConnectionError as e:
        return None, f"Impossibile raggiungere Redis su {REDIS_HOST}:{REDIS_PORT} — {e}"
    except Exception as e:
        return None, f"Errore leggendo lo stato: {e}"


def render(placeholder):
    with placeholder.container():
        st.title("Falls Game — Matchmaking Dashboard")

        client = get_redis_client()
        status, error = load_status(client)

        if error:
            st.error(error)
            return
        if status is None:
            st.info("Nessuno stato ancora pubblicato — il matchmaking server è acceso?")
            return

        pending = status.get("pending", [])
        active_games = status.get("active_games", {})
        timestamp = status.get("timestamp", 0)

        col1, col2 = st.columns(2)
        col1.metric("Giocatori in coda", len(pending))
        col2.metric("Partite attive", len(active_games))

        st.divider()

        st.subheader("In coda")
        if pending:
            st.dataframe(
                [{"ID": p["id"], "IP": p["ip"], "Porta UDP": p["udp_port"]} for p in pending],
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("Nessun giocatore in attesa al momento.")

        st.subheader("Partite attive")
        if active_games:
            rows = []
            for port, info in active_games.items():
                started = info.get("start_ts", 0)
                elapsed = time.time() - started if started else 0
                rows.append({
                    "Porta proxy": port,
                    "Giocatori": ", ".join(str(p) for p in info.get("players", [])),
                    "Durata": f"{int(elapsed // 60)}m {int(elapsed % 60)}s",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("Nessuna partita attiva al momento.")

        st.divider()
        if timestamp:
            st.caption(f"Ultimo aggiornamento (matchmaking server): "
                       f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}")
        st.caption(f"Questa pagina si aggiorna da sola ogni {REFRESH_SECONDS} secondi.")


placeholder = st.empty()
while True:
    render(placeholder)
    time.sleep(REFRESH_SECONDS)
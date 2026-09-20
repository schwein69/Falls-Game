from ursina import *
from player import RemotePlayer
import game_state
from usefulFunctions import clear_all_ui_elements


# Giocatori: spawn, posizione
def rpc_spawn_player(pid, x, y, z, status="alive", matchId=None):
    """Ricevuto quando un altro giocatore si unisce/spawna: creiamo la sua RemotePlayer."""
    nm = game_state.network_manager
    if nm and pid != nm.player_id:
        game_state.connected_ids.add(pid)

    if game_state.level_loaded:
        if pid not in game_state.other_players and pid != nm.player_id:
            print(f"[Game] Spawning Remote Player {pid}")
            new_p = RemotePlayer(position=Vec3(x, y, z), username=f"Player {pid}")
            game_state.other_players[pid] = new_p


def rpc_update_pos(pid, x, y, z):
    """Segnalazione della posizione di un giocatore all'autorita' di gioco (host P2P o game
    instance online). Non sposta piu' direttamente i giocatori remoti sullo schermo: quello lo
    fa rpc_state_snapshot, applicato a intervalli fissi dall'autorita'. Qui, se siamo noi l'host
    P2P, ci limitiamo a registrare il dato; se non lo siamo, questo pacchetto non dovrebbe
    arrivarci comunque (i client mandano update_pos solo all'host/server)."""
    nm = game_state.network_manager
    if game_state.game_authority is not None and nm and nm.is_host:
        game_state.game_authority.handle_update_pos(pid, x, y, z)


def rpc_state_snapshot(positions):
    """Stato consolidato di TUTTI i giocatori, applicato a intervalli fissi dall'autorita' di
    gioco (host P2P o game instance online). positions e' un dict {pid: [x,y,z]} — le chiavi
    arrivano come stringhe perche' JSON serializza sempre le chiavi dict come testo."""
    nm = game_state.network_manager
    for pid_str, pos in positions.items():
        pid = int(pid_str)
        if nm and pid == nm.player_id:
            continue  # non spostiamo noi stessi in base al nostro stesso pacchetto
        if pid in game_state.other_players:
            game_state.other_players[pid].position = Vec3(*pos)
        elif game_state.level_loaded:
            rpc_spawn_player(pid, *pos)


# Floor
def rpc_block_step(pid, block_id):
    """SOLO l'host P2P valida questa richiesta (e' lui l'autorita' quando giochiamo in locale;
    in modalita' online arriva direttamente al processo server/game_instance.py, non a noi).
    Un client normale non dovrebbe mai ricevere questo RPC (i client lo mandano solo all'host),
    ma per sicurezza controlliamo comunque prima di agire."""
    nm = game_state.network_manager
    if not (nm and nm.is_host and game_state.game_authority is not None):
        return
    result = game_state.game_authority.handle_block_step(pid, block_id)
    if result is None:
        return  
    apply_block_destroyed(result["block_id"], result["pid"])
    nm.broadcast_rpc("block_destroyed", result["block_id"], result["pid"])


def rpc_block_destroyed(block_id, pid):
    """Evento CANONICO: l'autorita' ha confermato che questo blocco e' stato distrutto.
    Applicato da tutti i client (per l'host P2P, questo handler gestisce solo la ricezione
    dal network: l'host applica l'evento direttamente in rpc_block_step, vedi sopra)."""
    apply_block_destroyed(block_id, pid)


def apply_block_destroyed(block_id, pid):
    """Distrugge visivamente il blocco corrispondente e applica l'eventuale effetto
    (velocita'/super salto) SOLO se il giocatore che lo ha attivato siamo noi."""
    if game_state.floors is None:
        return
    cube = game_state.floors.blocks_by_id.get(block_id)
    if cube is None:
        return
    nm = game_state.network_manager
    is_me = nm and pid == nm.player_id
    cube.on_authoritative_step(game_state.player if is_me else None)


def rpc_sync_state(destroyed_ids, positions):
    """Ricevuto solo da chi si e' appena unito o riconnesso a una partita gia' in corso: ci
    mette in pari con lo stato che l'autorita' ha gia' accumulato (blocchi gia' consumati dagli
    altri, posizioni correnti)."""
    if game_state.floors is not None:
        for block_id in destroyed_ids:
            cube = game_state.floors.blocks_by_id.get(block_id)
            if cube is not None and not cube.is_disappearing and not cube.has_activated:
                cube.force_destroy()
    rpc_state_snapshot(positions)


# Vita/morte, vittoria
def request_player_died():
    """Il giocatore locale e' caduto/eliminato: lo segnaliamo all'autorita' di gioco (host P2P
    o game instance online), che tiene il conto di chi e' ancora in gara e decide quando la
    partita e' finita (ultimo rimasto vivo)."""
    nm = game_state.network_manager
    if not nm or nm.player_id is None:
        return
    if nm.is_host:
        rpc_player_died(nm.player_id)
    else:
        nm.send_rpc("player_died", nm.player_id)


def apply_player_removed(pid):
    """Rimuove il 'fantasma' visivo di un giocatore appena morto/eliminato — la sua RemotePlayer
    smette di esistere per tutti, invece di restare congelata per sempre all'ultima posizione
    nota (una volta morto, il suo client smette di mandare aggiornamenti di posizione)."""
    if pid in game_state.other_players:
        destroy(game_state.other_players[pid])
        del game_state.other_players[pid]


def rpc_player_removed(pid):
    """Evento CANONICO: l'autorita' ha confermato che questo giocatore e' morto ed e' da
    rimuovere dalla vista (per l'host, questo handler gestisce solo la ricezione dal network:
    l'host applica l'evento direttamente in rpc_player_died, vedi sotto)."""
    apply_player_removed(pid)


def rpc_player_died(pid):
    """SOLO l'host/server valida chi e' ancora vivo (e' lui l'autorita'). Un client normale non
    dovrebbe mai ricevere questo RPC (i client lo mandano solo all'host), ma per sicurezza
    controlliamo comunque prima di agire."""
    nm = game_state.network_manager
    if not (nm and nm.is_host and game_state.game_authority is not None):
        return
    game_over, winner_pid = game_state.game_authority.handle_player_died(pid)
    # Rimuoviamo SEMPRE il "fantasma" di chi e' appena morto, a prescindere dal fatto che la
    # sua morte decida anche la fine della partita (con 3+ giocatori, chi muore ma non e' l'ultimo
    # deve comunque sparire dalla vista di tutti, non restare congelato per il resto della partita).
    apply_player_removed(pid)  # l'host non riceve mai i propri broadcast: applichiamo anche qui
    nm.broadcast_rpc("player_removed", pid)
    if game_over:
        # L'host e' anche un giocatore: applichiamo l'evento localmente (l'host non riceve mai
        # i propri broadcast via rete) E lo trasmettiamo a tutti gli altri. winner_pid puo'
        # essere None (nessuno rimasto vivo: partita finita comunque, senza un vincitore).
        apply_game_won(winner_pid)
        nm.broadcast_rpc("game_won", winner_pid)


def rpc_game_won(winner_pid):
    """Evento CANONICO: l'autorita' ha decretato un vincitore. Applicato da tutti (per l'host
    P2P questo handler gestisce solo la ricezione dal network: l'host applica l'evento
    direttamente in rpc_player_died, vedi sopra)."""
    apply_game_won(winner_pid)


def apply_game_won(winner_pid):
    """Mostra la schermata di fine partita a tutti — sia a chi ha vinto sia a chi e' gia' caduto
    in attesa. Riusa il flag game_over gia' esistente per congelare il resto della logica di
    gioco (vedi update() in main.py), cosi' non serve un flag separato."""
    if game_state.game_over:
        return
    game_state.game_over = True
    mouse.locked = False
    mouse.visible = True

    if game_state.player is not None:
        destroy(game_state.player)
        game_state.player = None

    clear_all_ui_elements()
    nm = game_state.network_manager
    if winner_pid is None:
        # Nessuno rimasto vivo (es. partita da soli, o coincidenza in cui muoiono tutti insieme):
        # la partita finisce comunque, semplicemente senza dichiarare un vincitore specifico.
        message = "Game Over"
    else:
        is_me = nm and winner_pid == nm.player_id
        message = "Hai vinto!" if is_me else f"Ha vinto il giocatore {winner_pid}!"
    Text(message, scale=2.5, origin=(0, 0), position=(0, 0.1), parent=camera.ui, color=color.gold)

    from game_lifecycle import resetFloor
    Button('Torna al menu', y=-0.15, scale=(0.3, 0.08), parent=camera.ui, on_click=resetFloor)


def rpc_player_left(pid):
    """Ricevuto quando un giocatore si disconnette: distruggiamo la sua RemotePlayer e lo
    togliamo dal conteggio dell'autorita'."""
    if pid in game_state.other_players:
        print(f"[Game] Player {pid} Left")
        destroy(game_state.other_players[pid])
        del game_state.other_players[pid]
    if game_state.game_authority is not None:
        # Chi si disconnette non "muore": esce e basta dal conteggio. Se questo lascia un solo
        # giocatore in gara, vince comunque lui (la partita non avrebbe piu' senso altrimenti).
        game_over, winner_pid = game_state.game_authority.remove_player(pid)
        if game_over and game_state.network_manager:
            apply_game_won(winner_pid)
            game_state.network_manager.broadcast_rpc("game_won", winner_pid)


# Ciclo della partita
def rpc_start_game(seed=None):
    """Ricevuto quando l'host avvia la partita. Il "catch-up" di chi era gia' in lobby avviene
    in gameStart() (0.1s dopo, vedi game_lifecycle.py) — qui level_loaded e' ancora False,
    quindi rpc_spawn_player non farebbe nulla."""
    print("[Game] Host started the game")
    game_state.lobby_open = False
    game_state.current_floor_seed = seed

    # Import ritardato: game_lifecycle.py a sua volta importa rpc_spawn_player da QUESTO modulo
    # (dentro gameStart) — vedi la nota li' per il perche' del ciclo spezzato qui.
    from game_lifecycle import loadLevel
    loadLevel(seed)


def rpc_game_pause(is_paused, pid=None):
    """Mette in pausa/riprende la partita (es. durante una disconnessione temporanea)."""
    game_state.game_paused = is_paused
    if is_paused:
        Text(f"Waiting for Player {pid} to reconnect...", scale=1.5, origin=(0, 0), y=0.1, tag='pause_text')
    else:
        print("[Game] RESUMED!")
        for t in scene.entities:
            if hasattr(t, 'tag') and t.tag == 'pause_text':
                destroy(t)


# Dispatch
RPC_REGISTRY = {
    "spawn_player": rpc_spawn_player,
    "update_pos": rpc_update_pos,
    "state_snapshot": rpc_state_snapshot,
    "block_step": rpc_block_step,
    "block_destroyed": rpc_block_destroyed,
    "sync_state": rpc_sync_state,
    "player_left": rpc_player_left,
    "player_died": rpc_player_died,
    "player_removed": rpc_player_removed,
    "game_won": rpc_game_won,
    "start_game": rpc_start_game,
    "game_pause": rpc_game_pause,
}


def handle_rpc(data):
    """Dispatches JSON data to functions"""
    method = data.get("method")
    args = data.get("args", [])
    if method in RPC_REGISTRY:
        RPC_REGISTRY[method](*args)


def request_block_step(block_id):
    nm = game_state.network_manager
    if not nm or nm.player_id is None:
        return
    if nm.is_host:
        rpc_block_step(nm.player_id, block_id)
    else:
        nm.send_rpc("block_step", nm.player_id, block_id)
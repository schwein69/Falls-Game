import sys
import os
import time
import socket
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import network
from network import NetworkManager
import network_p2p


import itertools
_ports = itertools.count(19100)


def _next_port():
    """Una porta diversa per ogni test (id(self) poteva ripetersi e far scontrare due test)."""
    return next(_ports) + 1


def _collect_methods(nm, timeout=0.5):
    """ritorna la lista dei metodi handler ricevuti"""
    received = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        while not nm.msg_queue.empty():
            received.append(nm.msg_queue.get_nowait().get("method"))
        time.sleep(0.02)
    return received


def _pauses(nm, timeout=0.5):
    """ritorna i game_pause ricevuti, come lista di True (pausa) / False (ripresa)"""
    received = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        while not nm.msg_queue.empty():
            msg = nm.msg_queue.get_nowait()
            if msg.get("method") == "game_pause":
                received.append(msg["args"][0])
        time.sleep(0.02)
    return received


class TestMultiClientRoster(unittest.TestCase):
    """Copre: 'avviare piu' istanze e vedere tutti i giocatori correttamente'.

    Usa fino a 3 client + l'host = 4 partecipanti in un'unica partita: piu' del vero
    MAX_PLAYERS di gioco (2), quindi qui lo alziamo SOLO per questa classe di test, senza
    toccare config.py (che resta a 2, il valore vero usato in partita)."""

    def setUp(self):
        self._max_players_patch = patch.object(network, "MAX_PLAYERS", 10)
        self._max_players_patch.start()
        self.port = _next_port()  # porta diversa per ogni test
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)
        self.host.room.host_plays = True
        self.clients = []

    def tearDown(self):
        self.host.stop()
        for c in self.clients:
            c.stop()
        self._max_players_patch.stop()

    def _join(self):
        c = NetworkManager()
        c.join_network("127.0.0.1", self.port)
        self.clients.append(c)
        time.sleep(0.2)
        return c

    def test_first_client_learns_about_host(self):
        c1 = self._join()
        methods = _collect_methods(c1)
        self.assertIn("spawn_player", methods,
                       "il primo client deve imparare dell'host tramite il catch-up")

    def test_third_client_learns_about_everyone_already_connected(self):
        c1 = self._join()
        c2 = self._join()
        c3 = self._join()
        methods = _collect_methods(c3)
        spawn_count = methods.count("spawn_player")
        self.assertGreaterEqual(spawn_count, 3,
                                 "il terzo arrivato deve sapere di host, client1 e client2")

    def test_window_close_during_match_pauses_then_removes_after_grace_period(self):
        """DISCONNECT (finestra chiusa di colpo) a partita in corso: pausa subito, rimozione
        solo dopo il tempo di grazia."""
        self.host.room.match_started = True
        c1 = self._join()
        c2 = self._join()
        _collect_methods(c2, timeout=0.3)

        c1.stop()  # non volontario -> manda DISCONNECT
        subito = _collect_methods(c2, timeout=0.3)
        self.assertIn("game_pause", subito)
        self.assertNotIn("player_left", subito)

        self.host.check_client_liveness(timeout=0.1, grace_period=0.2)
        time.sleep(0.3)
        self.host.check_client_liveness(timeout=0.1, grace_period=0.2)
        dopo = _collect_methods(c2, timeout=0.3)
        self.assertIn("player_left", dopo)

    def test_deliberate_leave_during_match_is_immediate_and_final(self):
        """LEAVE (scelta volontaria) a partita in corso: via subito, nessuna pausa per gli altri."""
        self.host.room.match_started = True
        c1 = self._join()
        c2 = self._join()
        _collect_methods(c2, timeout=0.3)

        c1.leaving_deliberately = True
        c1.stop()
        metodi = _collect_methods(c2, timeout=0.5)
        self.assertIn("player_left", metodi)
        self.assertNotIn("game_pause", metodi)

    def test_window_close_in_lobby_is_immediate(self):
        """Fuori partita (lobby) check_client_liveness non gira mai: una pausa non avrebbe
        modo di concludersi. Chi esce va tolto subito, come sempre."""
        self.assertFalse(self.host.room.match_started)
        c1 = self._join()
        c2 = self._join()
        _collect_methods(c2, timeout=0.3)

        c1.stop()
        metodi = _collect_methods(c2, timeout=0.5)
        self.assertIn("player_left", metodi)
        self.assertNotIn("game_pause", metodi)


class TestRejoinAfterRestart(unittest.TestCase):
    """Rientro di un client che ha chiuso l'app e l'ha riaperta (nuovo processo, nuova porta UDP)."""

    def setUp(self):
        self._p = patch.object(network, "MAX_PLAYERS", 10)
        self._p.start()
        self.port = _next_port()
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)
        self.host.room.host_plays = True
        self.host.room.match_started = True
        self.all = []

    def tearDown(self):
        self.host.stop()
        for c in self.all:
            c.stop()
        self._p.stop()

    def _join(self, rejoin_id=None):
        c = NetworkManager()
        c.join_network("127.0.0.1", self.port, rejoin_id=rejoin_id)
        self.all.append(c)
        time.sleep(0.4)
        return c

    def test_rejoin_while_paused_keeps_id_resumes_only_when_ready_and_syncs_state(self):
        c1 = self._join()
        c2 = self._join()
        old_id = c1.player_id
        _collect_methods(c2, timeout=0.2)

        # il server conosce la posizione di c1 e un blocco gia' distrutto
        self.host.room.on_join = lambda pid, addr: self.host.send_to(
            addr, "sync_state", [7], {str(pid): [1.0, 2.0, 3.0]})

        c1.stop()  # finestra chiusa di colpo
        self.assertIn("game_pause", _collect_methods(c2, timeout=0.3))
        self.assertEqual(len(self.host.room.silent), 1)

        nuovo = self._join(rejoin_id=old_id)  # nuovo processo, nuova porta
        self.assertEqual(nuovo.player_id, old_id, "deve riavere lo STESSO id")
        self.assertEqual(len(self.host.room.silent), 0, "non e' piu' in pausa/scollegato")
        self.assertEqual(len(self.host.room.clients), 2, "stesso numero di giocatori, nessun fantasma")

        # gli altri NON riprendono finche' chi rientra non ha finito di caricare il livello...
        self.assertEqual(_pauses(c2, timeout=0.4), [], "la ripresa non deve partire all'ingresso")
        nuovo.send_message("rejoin_done", old_id)
        # ...poi tutti riprendono insieme
        self.assertEqual(_pauses(c2, timeout=0.4), [False])
        # chi rientra riceve lo stato per rimettersi dov'era
        ricevuti = []
        while not nuovo.msg_queue.empty():
            ricevuti.append(nuovo.msg_queue.get_nowait())
        sync = [m for m in ricevuti if m.get("method") == "sync_state"]
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync[0]["args"][1][str(old_id)], [1.0, 2.0, 3.0])

    def test_rejoin_before_server_noticed_the_disconnect(self):
        """App riaperta cosi' in fretta che il server non aveva ancora nemmeno notato la sparizione."""
        c1 = self._join()
        old_id = c1.player_id
        c1.running = False           # crash: nessun DISCONNECT, il server non sa nulla
        nuovo = self._join(rejoin_id=old_id)
        self.assertEqual(nuovo.player_id, old_id)
        self.assertEqual(len(self.host.room.clients), 1, "il vecchio indirizzo e' stato sostituito")

    def test_rejoin_after_grace_period_is_refused_in_p2p(self):
        c1 = self._join()
        old_id = c1.player_id
        c1.stop()
        self.host.check_client_liveness(timeout=0.1, grace_period=0.1)
        time.sleep(0.2)
        self.host.check_client_liveness(timeout=0.1, grace_period=0.1)  # ormai rimosso
        self.assertEqual(len(self.host.room.clients), 0)

        nuovo = NetworkManager()
        nuovo.join_network("127.0.0.1", self.port, rejoin_id=old_id)
        self.all.append(nuovo)
        deadline = time.time() + 3
        while nuovo.join_rejected is None and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(nuovo.join_rejected)
        self.assertIn("scaduto", nuovo.join_rejected)
        self.assertIsNone(nuovo.player_id, "non deve ricevere un id nuovo a partita in corso")

    def test_rejoin_in_online_mode_with_new_udp_port(self):
        """Online: la porta nuova NON e' tra quelle pre-autorizzate, ma il posto esiste ancora:
        il rientro deve funzionare lo stesso."""
        self.host.stop()
        self.host = NetworkManager()
        a = NetworkManager(); pa = a.prepare_local_socket(); self.all.append(a)
        self.host.room.expected = {("127.0.0.1", pa): 4242}
        self.host.setup_host(port=self.port + 300)
        self.host.room.match_started = True
        a.join_network("127.0.0.1", self.port + 300)
        time.sleep(0.4)
        self.assertEqual(a.player_id, 4242)
        a.stop()

        nuovo = NetworkManager()
        nuovo.join_network("127.0.0.1", self.port + 300, rejoin_id=4242)
        self.all.append(nuovo)
        time.sleep(0.5)
        self.assertEqual(nuovo.player_id, 4242)


    def test_others_wait_while_rejoiner_loads_and_it_is_not_flagged_silent(self):
        c1 = self._join()
        c2 = self._join()
        old_id = c1.player_id
        _collect_methods(c2, timeout=0.2)
        c1.stop()
        self.assertEqual(_pauses(c2, timeout=0.3), [True])
        nuovo = self._join(rejoin_id=old_id)
        self.assertIn(old_id, self.host.room.rejoining)
        # il caricamento dura piu' del timeout di silenzio: non deve essere segnato "disconnesso" ne' rimosso
        time.sleep(0.4)
        self.host.check_client_liveness(timeout=0.1, grace_period=0.1)
        self.assertEqual(len(self.host.room.silent), 0)
        self.assertEqual(len(self.host.room.clients), 2)
        self.assertEqual(_pauses(c2, timeout=0.2), [])
        nuovo.send_message("rejoin_done", old_id)
        self.assertEqual(_pauses(c2, timeout=0.4), [False])
        self.assertNotIn(old_id, self.host.room.rejoining)

    def test_rejoin_before_server_noticed_pauses_the_others_until_ready(self):
        c1 = self._join()
        c2 = self._join()
        old_id = c1.player_id
        _collect_methods(c2, timeout=0.2)
        c1.running = False                       # crash: nessun DISCONNECT, il server non sa nulla
        nuovo = self._join(rejoin_id=old_id)
        self.assertEqual(_pauses(c2, timeout=0.4), [True], "mentre carica gli altri non devono andare avanti")
        nuovo.send_message("rejoin_done", old_id)
        self.assertEqual(_pauses(c2, timeout=0.4), [False])

    def test_rejoiner_that_leaves_while_loading_resumes_the_others(self):
        c1 = self._join()
        c2 = self._join()
        old_id = c1.player_id
        _collect_methods(c2, timeout=0.2)
        c1.stop()
        _pauses(c2, timeout=0.3)
        nuovo = self._join(rejoin_id=old_id)
        nuovo.leaving_deliberately = True        # rientro fallito: il client torna al menu
        nuovo.stop()
        time.sleep(0.3)
        methods = []
        while not c2.msg_queue.empty():
            methods.append(c2.msg_queue.get_nowait())
        self.assertIn(False, [m["args"][0] for m in methods if m.get("method") == "game_pause"], "gli altri devono riprendere")
        self.assertIn("player_left", [m.get("method") for m in methods])
        self.assertEqual(len(self.host.room.rejoining), 0)

    def test_rejoiner_that_never_finishes_loading_is_removed_and_the_others_resume(self):
        c1 = self._join()
        c2 = self._join()
        old_id = c1.player_id
        _collect_methods(c2, timeout=0.2)
        c1.stop()
        _pauses(c2, timeout=0.3)
        self._join(rejoin_id=old_id)             # rientra ma non manda mai rejoin_done
        with patch.object(network, "REJOIN_LOAD_TIMEOUT", 0.2):
            time.sleep(0.3)
            self.host.check_client_liveness(timeout=5, grace_period=5)
        methods = []
        deadline = time.time() + 0.4
        while time.time() < deadline:
            while not c2.msg_queue.empty():
                methods.append(c2.msg_queue.get_nowait())
            time.sleep(0.02)
        self.assertIn(False, [m["args"][0] for m in methods if m.get("method") == "game_pause"])
        self.assertIn("player_left", [m.get("method") for m in methods])
        self.assertEqual(len(self.host.room.clients), 1)
        self.assertEqual(len(self.host.room.rejoining), 0)


class TestAntiSpoofing(unittest.TestCase):
    def setUp(self):
        self.port = _next_port()
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)

    def tearDown(self):
        self.host.stop()

    def test_packet_with_wrong_sender_id_is_rejected(self):
        import json
        client = NetworkManager()
        client.join_network("127.0.0.1", self.port)
        time.sleep(0.2)

        real_id = client.player_id
        fake_id = real_id + 99

        malicious = json.dumps({"method": "update_pos", "args": [fake_id, 1, 2, 3], "sender_id": fake_id})
        client.sock.sendto(malicious.encode(), (client.host_ip, client.host_port))
        time.sleep(0.2)

        methods = _collect_methods(self.host, timeout=0.3)
        self.assertNotIn("update_pos", methods,
                          "un pacchetto con sender_id falsificato non deve essere accettato")
        client.stop()


class TestHostMigration(unittest.TestCase):
    """Copre: 'testare la migrazione dell'host'. Simula host + 2 client, uccide l'host, e
    verifica che il sopravvissuto con id piu' basso possa promuoversi correttamente e che
    l'altro possa poi riconnettersi mantenendo lo stesso id.

    Host + 2 client = 3 partecipanti: piu' del vero MAX_PLAYERS di gioco (2), quindi lo
    alziamo SOLO per questa classe, senza toccare config.py."""

    def setUp(self):
        self._max_players_patch = patch.object(network, "MAX_PLAYERS", 10)
        self._max_players_patch.start()
        self.port = _next_port()

    def tearDown(self):
        self._max_players_patch.stop()

    def test_promotion_keeps_identity_and_reconnect_gets_same_id(self):
        host = network_p2p.NetworkManager()
        host.allow_host_migration = True
        host.setup_host(port=self.port)
        host.room.host_plays = True

        c1 = network_p2p.NetworkManager()
        c1.allow_host_migration = True
        c1.join_network("127.0.0.1", self.port)
        time.sleep(0.2)

        c2 = network_p2p.NetworkManager()
        c2.allow_host_migration = True
        c2.join_network("127.0.0.1", self.port)
        time.sleep(0.2)

        survivor_ids = {c1.player_id, c2.player_id}
        elected_id = min(survivor_ids)
        self.assertEqual(elected_id, c1.player_id,
                          "il primo client ad unirsi ha sempre l'id piu' basso tra i due client")

        # L'host "sparisce" senza avvisare nessuno (simula un crash, non un leave pulito).
        host.running = False
        host.sock.close()

        # c1 (id piu' basso) si promuove a nuovo host, mantenendo il proprio id.
        new_nm, authority, broadcaster = network_p2p.promote_to_host(
            c1, floor_seed=12345, destroyed_blocks=[], player_positions={}, survivor_ids=survivor_ids)
        try:
            self.assertEqual(new_nm.player_id, elected_id,
                              "il promosso deve mantenere lo stesso id di prima, non diventare 0")
            self.assertTrue(new_nm.is_host)
            self.assertEqual(authority.floor_seed, 12345,
                              "il seed del pavimento va preservato, non rigenerato a caso")

            # c2 si ricollega chiedendo di riavere lo stesso id di prima.
            old_c2_id = c2.player_id
            c2.stop()
            reconnected = network_p2p.reconnect_to_new_host(old_c2_id, "127.0.0.1", new_nm.host_port)
            time.sleep(0.3)
            self.assertEqual(reconnected.player_id, old_c2_id,
                              "chi si ricollega dopo la migrazione deve riavere lo stesso id")
            reconnected.stop()
        finally:
            new_nm.stop()


class TestClientDisconnectPauseAndRejoin(unittest.TestCase):
    """Copre: 'la pausa e il rientro al match' — un client che smette di mandare pacchetti
    (calo di rete, non un'uscita volontaria) deve mettere tutti in pausa, e se torna a farsi
    sentire in tempo la partita riprende senza bisogno di nessun protocollo di rientro speciale.
    Usiamo timeout/grace_period MOLTO corti (passati esplicitamente) per non far durare i test
    minuti interi con i valori reali di config.py."""

    def setUp(self):
        self.port = _next_port()
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)
        self.host.room.host_plays = True
        # Il controllo del silenzio vale solo a partita iniziata (vedi match_in_progress in
        # network.py: in lobby un client non manda nulla, non e' "disconnesso"). Questi test
        # simulano una partita in corso.
        self.host.room.match_started = True

    def tearDown(self):
        self.host.stop()

    def test_silent_client_triggers_pause_then_gets_removed_if_never_returns(self):
        client = NetworkManager()
        client.join_network("127.0.0.1", self.port)
        time.sleep(0.2)
        client.send_message("update_pos", client.player_id, 0, 0, 0)

        # Aspettiamo PIU' del timeout (0.3s) senza mandare nient'altro: deve risultare sospetto.
        time.sleep(0.4)
        self.host.check_client_liveness(timeout=0.3, grace_period=0.5)
        self.assertIn(client.player_id, [pid for _, (pid, _) in self.host.room.silent.items()],
                       "dopo il timeout di silenzio, il client deve risultare 'in pausa'")

        # Il tempo di grazia (0.5s) scade senza che si faccia risentire: viene rimosso per davvero.
        time.sleep(0.6)
        self.host.check_client_liveness(timeout=0.3, grace_period=0.5)
        remaining_pids = list(self.host.room.clients.values())
        self.assertNotIn(client.player_id, remaining_pids,
                          "dopo la scadenza della grazia, il client va rimosso per davvero")
        client.stop()

    def test_client_that_resumes_traffic_is_not_removed(self):
        client = NetworkManager()
        client.join_network("127.0.0.1", self.port)
        time.sleep(0.2)
        client.send_message("update_pos", client.player_id, 0, 0, 0)  # stabilisce un "ultimo visto"

        # Aspettiamo piu' di un timeout molto breve, cosi' risulta sospetto...
        time.sleep(0.15)
        self.host.check_client_liveness(timeout=0.1, grace_period=5.0)
        self.assertTrue(len(self.host.room.silent) > 0)

        # ...ma poi ricomincia a mandare pacchetti PRIMA che scada la grazia (5s): deve
        # rientrare da solo, senza essere rimosso.
        client.send_message("update_pos", client.player_id, 1, 1, 1)
        time.sleep(0.2)

        self.assertEqual(len(self.host.room.silent), 0,
                          "riprendendo a mandare pacchetti, il client deve rientrare da solo")
        self.assertIn(client.player_id, self.host.room.clients.values(),
                       "il client rimane registrato, non viene mai rimosso")
        client.stop()


class TestJoinReliability(unittest.TestCase):
    """Regressioni trovate misurando la catena Online reale, e poi il passaggio a un handshake
    TCP dedicato per l'ingresso (vedi network.py: setup_host apre anche un socket TCP sulla
    stessa porta, solo per assegnare gli id in modo affidabile — il gioco vero resta su UDP)."""

    def setUp(self):
        self.port = _next_port()
        self.server = None
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.stop()
        if self.server:
            self.server.stop()

    def test_client_joins_even_if_server_starts_late(self):
        self.client = NetworkManager()
        self.client.join_network("127.0.0.1", self.port)   # server ancora spento: il primo CONNECT fallisce
        time.sleep(1.0)
        self.server = NetworkManager()
        self.server.setup_host(port=self.port)              # parte in ritardo
        deadline = time.time() + 4
        while self.client.player_id is None and time.time() < deadline:
            time.sleep(0.1)
        self.assertIsNotNone(self.client.player_id,
                             "il client doveva ritentare il CONNECT TCP e ottenere un id")

    def test_repeated_join_from_same_address_is_idempotent(self):
        """L'handshake TCP di per se' non perde risposte (a differenza del vecchio UDP), ma
        deve restare idempotente lo stesso: se per qualunque motivo arrivano piu' richieste
        dallo stesso indirizzo UDP (stesso ip, stessa porta), l'host deve rimandare sempre lo
        STESSO id, non registrarne uno nuovo ogni volta."""
        self.server = NetworkManager()
        self.server.setup_host(port=self.port)
        self.client = NetworkManager()
        self.client.join_network("127.0.0.1", self.port)
        time.sleep(0.3)
        first_id = self.client.player_id
        self.assertIsNotNone(first_id)

        # Una seconda richiesta di ingresso, esplicita, dallo STESSO indirizzo UDP del client
        # gia' connesso — come farebbe un ritentativo capitato per sbaglio due volte.
        my_udp_port = self.client.sock.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.settimeout(3)
            tcp.connect(("127.0.0.1", self.port))
            tcp.sendall(json.dumps({"udp_port": my_udp_port, "rejoin_id": None}).encode())
            resp = json.loads(tcp.recv(1024).decode())

        self.assertEqual(resp["id"], first_id,
                         "una richiesta ripetuta dallo stesso indirizzo deve riavere lo stesso id")
        self.assertEqual(len(self.server.room.clients), 1,
                         "non deve registrare un giocatore in piu' per lo stesso indirizzo")


class TestLobbyIsNotMistakenForDisconnect(unittest.TestCase):
    """Regressione del 'rematch bloccato': in lobby un client non manda pacchetti, quindi il
    controllo del silenzio NON deve scattare finche' la partita non e' iniziata."""

    def test_silence_in_lobby_is_ignored_until_match_starts(self):
        host = NetworkManager()
        host.is_host = True
        addr = ("127.0.0.1", 55555)
        host.room.clients[addr] = 1
        host.room.last_seen[addr] = time.time() - 30       # 30s di silenzio

        host.room.match_started = False                        # ancora in lobby
        host.check_client_liveness(timeout=5, grace_period=10)
        self.assertEqual(len(host.room.silent), 0)

        host.room.match_started = True                         # partita iniziata
        host.check_client_liveness(timeout=5, grace_period=10)
        self.assertEqual(len(host.room.silent), 1)



class TestClosedRoom(unittest.TestCase):
    """P2P: dopo lo Start dell'host nessun nuovo giocatore, ma chi era gia' dentro puo' rientrare."""

    def setUp(self):
        self.port = _next_port()
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)
        self.host.room.host_plays = True
        self.all = []

    def tearDown(self):
        self.host.stop()
        for c in self.all:
            c.stop()

    def _join(self, rejoin_id=None):
        c = NetworkManager()
        c.join_network("127.0.0.1", self.port, rejoin_id=rejoin_id)
        self.all.append(c)
        return c

    def test_new_player_refused_after_start(self):
        self._join()
        time.sleep(0.3)
        self.host.room.closed = True
        late = self._join()
        deadline = time.time() + 3
        while late.join_rejected is None and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn("gia' iniziata", late.join_rejected)
        self.assertIsNone(late.player_id)
        self.assertEqual(len(self.host.room.clients), 1)

    def test_rejoin_still_allowed_after_start(self):
        c1 = self._join()
        time.sleep(0.3)
        old_id = c1.player_id
        self.host.room.closed = True
        self.host.room.match_started = True
        c1.stop()
        self.host.check_client_liveness(timeout=0.1, grace_period=10)
        again = self._join(rejoin_id=old_id)
        time.sleep(0.5)
        self.assertEqual(again.player_id, old_id)

if __name__ == "__main__":
    unittest.main()
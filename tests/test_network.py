import sys
import os
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from network import NetworkManager
import network_p2p


def _collect_methods(nm, timeout=0.5):
    """ritorna la lista dei metodi RPC ricevuti"""
    received = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        while not nm.msg_queue.empty():
            received.append(nm.msg_queue.get_nowait().get("method"))
        time.sleep(0.02)
    return received


class TestMultiClientRoster(unittest.TestCase):
    """Copre: 'avviare piu' istanze e vedere tutti i giocatori correttamente'."""

    def setUp(self):
        self.port = 19100 + (id(self) % 500)  # porta diversa per ogni test
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)
        self.host.host_is_player = True
        self.clients = []

    def tearDown(self):
        self.host.stop()
        for c in self.clients:
            c.stop()

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

    def test_immediate_disconnect_no_grace_period(self):
        c1 = self._join()
        c2 = self._join()
        _collect_methods(c2, timeout=0.3)  # scarichiamo il rumore iniziale

        c1.stop()  # uscita volontaria: deve essere immediata, niente periodo di grazia
        methods = _collect_methods(c2, timeout=0.5)
        self.assertIn("player_left", methods)


class TestAntiSpoofing(unittest.TestCase):
    def setUp(self):
        self.port = 19600 + (id(self) % 300)
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

        # send_rpc() imposta SEMPRE "sender_id" al nostro vero player_id, quindi non basta per
        # testare un pacchetto davvero falsificato — costruiamo il pacchetto a mano, bypassando
        # l'API sicura, per verificare che sia la VALIDAZIONE DELL'HOST a bloccarlo.
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
    l'altro possa poi riconnettersi mantenendo lo stesso id."""

    def setUp(self):
        self.port = 19900 + (id(self) % 90)

    def test_promotion_keeps_identity_and_reconnect_gets_same_id(self):
        host = network_p2p.NetworkManager()
        host.allow_host_migration = True
        host.setup_host(port=self.port)
        host.host_is_player = True

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
            reconnected = network_p2p.reconnect_to_new_host(old_c2_id, "127.0.0.1")
            # Nota: promote_to_host manda in ascolto sulla porta P2P fissa (config.P2P_PORT),
            # non su self.port di questo test — per questo reconnect_to_new_host non riceve
            # una porta esplicita, usa quella di default.
            time.sleep(0.3)
            self.assertEqual(reconnected.player_id, old_c2_id,
                              "chi si ricollega dopo la migrazione deve riavere lo stesso id")
            reconnected.stop()
        finally:
            new_nm.stop()


if __name__ == "__main__":
    unittest.main()


class TestClientDisconnectPauseAndRejoin(unittest.TestCase):
    """Copre: 'la pausa e il rientro al match' — un client che smette di mandare pacchetti
    (calo di rete, non un'uscita volontaria) deve mettere tutti in pausa, e se torna a farsi
    sentire in tempo la partita riprende senza bisogno di nessun protocollo di rientro speciale.
    Usiamo timeout/grace_period MOLTO corti (passati esplicitamente) per non far durare i test
    minuti interi con i valori reali di config.py."""

    def setUp(self):
        self.port = 19700 + (id(self) % 200)
        self.host = NetworkManager()
        self.host.setup_host(port=self.port)
        self.host.host_is_player = True
        # Il controllo del silenzio vale solo a partita iniziata (vedi match_in_progress in
        # network.py: in lobby un client non manda nulla, non e' "disconnesso"). Questi test
        # simulano una partita in corso.
        self.host.match_in_progress = True

    def tearDown(self):
        self.host.stop()

    def test_silent_client_triggers_pause_then_gets_removed_if_never_returns(self):
        client = NetworkManager()
        client.join_network("127.0.0.1", self.port)
        time.sleep(0.2)
        client.send_rpc("update_pos", client.player_id, 0, 0, 0)

        # Aspettiamo PIU' del timeout (0.3s) senza mandare nient'altro: deve risultare sospetto.
        time.sleep(0.4)
        self.host.check_client_liveness(timeout=0.3, grace_period=0.5)
        self.assertIn(client.player_id, [pid for _, (pid, _) in self.host.disconnected_clients.items()],
                       "dopo il timeout di silenzio, il client deve risultare 'in pausa'")

        # Il tempo di grazia (0.5s) scade senza che si faccia risentire: viene rimosso per davvero.
        time.sleep(0.6)
        self.host.check_client_liveness(timeout=0.3, grace_period=0.5)
        remaining_pids = list(self.host.clients.values())
        self.assertNotIn(client.player_id, remaining_pids,
                          "dopo la scadenza della grazia, il client va rimosso per davvero")
        client.stop()

    def test_client_that_resumes_traffic_is_not_removed(self):
        client = NetworkManager()
        client.join_network("127.0.0.1", self.port)
        time.sleep(0.2)
        client.send_rpc("update_pos", client.player_id, 0, 0, 0)  # stabilisce un "ultimo visto"

        # Aspettiamo piu' di un timeout molto breve, cosi' risulta sospetto...
        time.sleep(0.15)
        self.host.check_client_liveness(timeout=0.1, grace_period=5.0)
        self.assertTrue(len(self.host.disconnected_clients) > 0)

        # ...ma poi ricomincia a mandare pacchetti PRIMA che scada la grazia (5s): deve
        # rientrare da solo, senza essere rimosso.
        client.send_rpc("update_pos", client.player_id, 1, 1, 1)
        time.sleep(0.2)

        self.assertEqual(len(self.host.disconnected_clients), 0,
                          "riprendendo a mandare pacchetti, il client deve rientrare da solo")
        self.assertIn(client.player_id, self.host.clients.values(),
                       "il client rimane registrato, non viene mai rimosso")
        client.stop()


class TestJoinReliability(unittest.TestCase):
    """Regressioni trovate misurando la catena Online reale: il JOIN_REQUEST UDP veniva mandato
    UNA volta sola. In Online il proxy e' pronto (TCP) prima che il processo primary abbia
    legato la sua porta UDP: se il pacchetto arrivava in quella finestra andava perso e il
    client restava senza id per sempre."""

    def setUp(self):
        self.port = 19400 + (id(self) % 200)
        self.server = None
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.stop()
        if self.server:
            self.server.stop()

    def test_client_joins_even_if_server_starts_late(self):
        self.client = NetworkManager()
        self.client.join_network("127.0.0.1", self.port)   # server ancora spento: primo pacchetto perso
        time.sleep(1.0)
        self.server = NetworkManager()
        self.server.setup_host(port=self.port)              # parte in ritardo
        deadline = time.time() + 4
        while self.client.player_id is None and time.time() < deadline:
            time.sleep(0.1)
        self.assertIsNotNone(self.client.player_id,
                             "il client doveva ritentare il JOIN e ottenere un id")

    def test_join_retries_never_create_phantom_players(self):
        """In P2P l'id e' len(clients)+1: senza idempotenza ogni ritentativo creerebbe un
        giocatore in piu'."""
        self.server = NetworkManager()
        self.server.setup_host(port=self.port)
        self.client = NetworkManager()
        self.client.join_network("127.0.0.1", self.port)
        time.sleep(0.3)
        # duplicati espliciti dallo STESSO indirizzo, come farebbe il ritentativo
        for _ in range(4):
            self.client.sock.sendto(b"JOIN_REQUEST", ("127.0.0.1", self.port))
            time.sleep(0.05)
        time.sleep(0.5)
        self.assertEqual(len(self.server.clients), 1,
                         "i JOIN_REQUEST duplicati non devono registrare giocatori in piu'")
        self.assertEqual(self.client.player_id, 1)


class TestLobbyIsNotMistakenForDisconnect(unittest.TestCase):
    """Regressione del 'rematch bloccato': in lobby un client non manda pacchetti, quindi il
    controllo del silenzio NON deve scattare finche' la partita non e' iniziata."""

    def test_silence_in_lobby_is_ignored_until_match_starts(self):
        host = NetworkManager()
        host.is_host = True
        addr = ("127.0.0.1", 55555)
        host.clients[addr] = 1
        host.client_last_seen[addr] = time.time() - 30       # 30s di silenzio

        host.match_in_progress = False                        # ancora in lobby
        host.check_client_liveness(timeout=5, grace_period=10)
        self.assertEqual(len(host.disconnected_clients), 0)

        host.match_in_progress = True                         # partita iniziata
        host.check_client_liveness(timeout=5, grace_period=10)
        self.assertEqual(len(host.disconnected_clients), 1)
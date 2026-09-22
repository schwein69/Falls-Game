"""
Test di integrazione per network.py/network_p2p.py: usano socket UDP su localhost (porte
diverse per ogni test, per non scontrarsi tra loro), ma nessuna finestra grafica — non serve
avviare Ursina. Coprono esattamente gli scenari da testare manualmente con piu' istanze:
piu' client connessi, disconnessione, migrazione dell'host.

Si lanciano con:
    python -m unittest tests.test_network_integration -v

"""
import sys
import os
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from network import NetworkManager
import network_p2p


def _collect_methods(nm, timeout=0.5):
    """Drena la coda dei messaggi in arrivo per un po' e ritorna la lista dei metodi RPC
    ricevuti (solo i nomi, per asserzioni semplici nei test)."""
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
        self.port = 19100 + (id(self) % 500)  # porta diversa per ogni test, riduce collisioni
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

        # L'host sparisce.
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
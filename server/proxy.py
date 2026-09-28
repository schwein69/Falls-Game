import sys
import os
import json
import socket
import subprocess
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from config import LOG_PREFIX, MAX_PLAYERS

GAME_INSTANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_instance.py")
HEALTH_CHECK_INTERVAL = 1.0  # secondi tra un controllo di vita del primary e il successivo


class MatchProxy:
    def __init__(self, tcp_port, players_info, udp_base_port):
        self.tcp_port = tcp_port
        self.players_info = players_info
        self.primary_port = udp_base_port
        self.backup_port = udp_base_port + 1
        self.backup_control_port = udp_base_port + 2
        self.replica_port = udp_base_port + 3

        self.client_sockets = []   # connessioni TCP persistenti verso i client di questa partita
        self.lock = threading.Lock()
        self.primary_process = None
        self.backup_process = None
        self.failover_done = False
        self.should_exit = threading.Event()

    # ------------------------------------------------------------------
    def start(self):
        self._spawn_primary()
        self._spawn_backup()
        threading.Thread(target=self._accept_clients, daemon=True).start()
        threading.Thread(target=self._health_check_loop, daemon=True).start()

    def _spawn_primary(self):
        args = [sys.executable, GAME_INSTANCE_PATH,
                "--role", "primary",
                "--udp-port", str(self.primary_port),
                "--control-port", "0",  # il primary non riceve mai comandi di controllo
                "--replica-port", str(self.replica_port),
                "--players", json.dumps(self.players_info)]
        self.primary_process = subprocess.Popen(args)
        print(f"{LOG_PREFIX}[Proxy] Primary avviato (pid processo {self.primary_process.pid}, "
              f"porta UDP {self.primary_port}).")

    def _spawn_backup(self):
        args = [sys.executable, GAME_INSTANCE_PATH,
                "--role", "backup",
                "--udp-port", str(self.backup_port),
                "--control-port", str(self.backup_control_port),
                "--replica-port", str(self.replica_port),
                "--players", json.dumps(self.players_info)]
        self.backup_process = subprocess.Popen(args)
        print(f"{LOG_PREFIX}[Proxy] Backup avviato (pid processo {self.backup_process.pid}, "
              f"porta UDP {self.backup_port}, in ascolto passivo).")

    # ------------------------------------------------------------------
    def _accept_clients(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", self.tcp_port))
        srv.listen(MAX_PLAYERS)
        print(f"{LOG_PREFIX}[Proxy] In ascolto client su porta TCP {self.tcp_port}.")
        while True:
            conn, addr = srv.accept()
            print(f"{LOG_PREFIX}[Proxy] Client connesso da {addr}.")
            with self.lock:
                self.client_sockets.append(conn)
            self._send_current_primary(conn)

    def _send_current_primary(self, conn):
        msg = json.dumps({"primary_host": "127.0.0.1", "primary_port": self.primary_port}) + "\n"
        try:
            conn.sendall(msg.encode())
        except Exception as e:
            print(f"{LOG_PREFIX}[Proxy] Errore mandando l'indirizzo del primary a un client: {e}")

    # ------------------------------------------------------------------
    def _health_check_loop(self):
        while not self.should_exit.is_set():
            time.sleep(HEALTH_CHECK_INTERVAL)
            current = self.backup_process if self.failover_done else self.primary_process
            code = current.poll()
            if code is None:
                continue  # ancora vivo, tutto normale

            if code == 0:
                print(f"{LOG_PREFIX}[Proxy] La partita e' finita (tutti i giocatori se ne sono "
                      f"andati). Chiudo il proxy.")
                self._shutdown()
                return

            if self.failover_done:
                print(f"{LOG_PREFIX}[Proxy] Anche il nuovo primary e' crashato (codice {code}), "
                      f"e non c'e' piu' un backup dietro. Chiudo.")
                self._shutdown()
                return

            print(f"{LOG_PREFIX}[Proxy] PRIMARY CRASHATO (codice uscita {code}). "
                  f"Avvio il failover...")
            self._failover()

    def _failover(self):
        """Promuove il backup e avvisa tutti i client"""
        try:
            ctrl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ctrl.settimeout(3.0)
            ctrl.connect(("127.0.0.1", self.backup_control_port))
            ctrl.sendall(b"PROMOTE")
            ctrl.close()
        except Exception as e:
            print(f"{LOG_PREFIX}[Proxy] Impossibile promuovere il backup: {e}")
            return

        self.failover_done = True
        msg = json.dumps({"new_primary_host": "127.0.0.1", "new_primary_port": self.backup_port}) + "\n"
        with self.lock:
            for conn in self.client_sockets:
                try:
                    conn.sendall(msg.encode())
                except Exception as e:
                    print(f"{LOG_PREFIX}[Proxy] Errore avvisando un client del failover: {e}")
        print(f"{LOG_PREFIX}[Proxy] Failover completato. Nuovo primary sulla porta UDP {self.backup_port}.")

    def _shutdown(self):
        for proc in (self.primary_process, self.backup_process):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        with self.lock:
            for conn in self.client_sockets:
                try:
                    conn.close()
                except Exception:
                    pass
        self.should_exit.set()


def main():
    if len(sys.argv) < 4:
        print(f"{LOG_PREFIX} Uso: proxy.py <porta_tcp> '<players_json>' <porta_udp_base>")
        sys.exit(1)

    tcp_port = int(sys.argv[1])
    players_info = json.loads(sys.argv[2])
    udp_base_port = int(sys.argv[3])

    proxy = MatchProxy(tcp_port, players_info, udp_base_port)
    proxy.start()

    try:
        proxy.should_exit.wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
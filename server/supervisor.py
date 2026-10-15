import sys
import os
import json
import socket
import subprocess
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from config import LOG_PREFIX

GAME_INSTANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_instance.py")
HEALTH_CHECK_INTERVAL = 1.0   # secondi tra un controllo di vita del server di gioco e il successivo
READY_TIMEOUT = 5.0           # quanto aspetto che il nuovo server dopo un crash sia pronto
STATE_PREFIX = "@STATE "      # righe speciali che il server scrive su stdout: ultimo stato della partita
READY_LINE = "@READY"         # il server e' in ascolto


class MatchSupervisor:
    def __init__(self, tcp_port, players_info, udp_base_port):
        self.tcp_port = tcp_port
        self.players_info = players_info
        self.server_port = udp_base_port      
        self.spare_port = udp_base_port + 1   #

        self.client_sockets = []   # connessioni TCP persistenti verso i client di questa partita
        self.lock = threading.Lock()
        self.server_process = None
        self.ready = threading.Event()
        self.last_state = None     # ultima riga di stato ricevuta: {"v": versione, "t": istante, "state": {...}}
        self.failover_done = False
        self.should_exit = threading.Event()

    def start(self):
        self._spawn_server(self.server_port)
        threading.Thread(target=self._accept_clients, daemon=True).start()
        threading.Thread(target=self._health_check_loop, daemon=True).start()

    def _spawn_server(self, port, saved=None):
        args = [sys.executable, GAME_INSTANCE_PATH, "--udp-port", str(port),
                "--players", json.dumps(self.players_info)]
        if saved is not None:
            args.append("--restore")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.ready = threading.Event()
        proc = subprocess.Popen(args, env=env, text=True, encoding="utf-8", errors="replace",
                                stdout=subprocess.PIPE, stdin=subprocess.PIPE if saved is not None else None)
        if saved is not None:
            proc.stdin.write(json.dumps(saved) + "\n")
            proc.stdin.close()
        self.server_process = proc
        threading.Thread(target=self._read_server_output, args=(proc, self.ready), daemon=True).start()
        print(f"{LOG_PREFIX}[Supervisor] Server di gioco avviato (pid {proc.pid}, porta {port}).")

    def _read_server_output(self, proc, ready):
        for line in proc.stdout:
            if line.startswith(STATE_PREFIX):
                try:
                    self.last_state = json.loads(line[len(STATE_PREFIX):])
                except ValueError:
                    pass
            elif line.startswith(READY_LINE):
                ready.set()
            else:
                sys.stdout.write(line)
                sys.stdout.flush()

    def _accept_clients(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", self.tcp_port))
        backlog = max(len(self.players_info) * 2, 8)
        srv.listen(backlog)
        print(f"{LOG_PREFIX}[Supervisor] In ascolto client su porta TCP {self.tcp_port} "
              f"(coda: {backlog}).")
        while True:
            conn, addr = srv.accept()
            print(f"{LOG_PREFIX}[Supervisor] Client connesso da {addr}.")
            with self.lock:
                self.client_sockets.append(conn)
            threading.Thread(target=self._send_current_server, args=(conn,), daemon=True).start()

    def _send_current_server(self, conn):
        msg = json.dumps({"primary_port": self.server_port}) + "\n"   
        try:
            conn.sendall(msg.encode())
        except Exception as e:
            print(f"{LOG_PREFIX}[Supervisor] Errore mandando la porta del server a un client: {e}")

    def _health_check_loop(self):
        while not self.should_exit.is_set():
            time.sleep(HEALTH_CHECK_INTERVAL)
            code = self.server_process.poll()
            if code is None:
                continue  

            if code == 0:
                print(f"{LOG_PREFIX}[Supervisor] La partita e' finita.")
                self._shutdown()
                return

            if self.failover_done:
                print(f"{LOG_PREFIX}[Supervisor] Anche il server ripartito e' crashato (codice {code}).")
                self._shutdown()
                return

            print(f"{LOG_PREFIX}[Supervisor] SERVER CRASHATO (codice uscita {code}). Lo faccio ripartire...")
            self._failover()

    def _failover(self):
        saved = self.last_state
        if saved is not None:
            print(f"{LOG_PREFIX}[Supervisor] Riparto dallo stato v{saved['v']} di {time.time() - saved['t']:.1f}s fa "
                  f"(quello che e' successo dopo si perde).")
        else:
            print(f"{LOG_PREFIX}[Supervisor] Nessuno stato salvato: riparto da zero.")
        self.failover_done = True
        self.server_port = self.spare_port
        self._spawn_server(self.spare_port, saved)
        if not self.ready.wait(READY_TIMEOUT):
            print(f"{LOG_PREFIX}[Supervisor] Il nuovo server non risulta pronto dopo {READY_TIMEOUT}s, avviso comunque.")

        msg = json.dumps({"new_primary_port": self.server_port}) + "\n"
        with self.lock:
            for conn in self.client_sockets:
                try:
                    conn.sendall(msg.encode())
                except Exception as e:
                    print(f"{LOG_PREFIX}[Supervisor] Errore avvisando un client del failover: {e}")
        print(f"{LOG_PREFIX}[Supervisor] Failover completato. Nuovo server sulla porta {self.server_port}.")

    def _shutdown(self):
        proc = self.server_process
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
        print(f"{LOG_PREFIX} Uso: supervisor.py <porta_tcp> '<players_json>' <porta_base_server>")
        sys.exit(1)

    tcp_port = int(sys.argv[1])
    players_info = json.loads(sys.argv[2])
    udp_base_port = int(sys.argv[3])

    supervisor = MatchSupervisor(tcp_port, players_info, udp_base_port)
    supervisor.start()

    try:
        supervisor.should_exit.wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
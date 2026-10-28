# Falls Game

A multiplayer arena game inspired by *Fall Guys*, written in Python with the [Ursina](https://www.ursinaengine.org/) engine. Players run on a multi-layered arena of blocks that disappear shortly after being stepped on. Fall off and you are out; the last player standing wins.

It is the final project of a Distributed Systems course: the interesting part is not the game itself, but how up to three PCs on the same local network keep **the same state** (same arena, same missing blocks, same winner), and what happens when something fails.

> The game is played on a local network only. Cloud hosting is future work.

## Features

- **Two ways to play**
  - **Local (P2P):** one player hosts and plays, the others find the host on the LAN (UDP broadcast).
  - **Matchmaking:** players join a queue; a server starts one `game_instance` process per match.
- **Authoritative server:** clients only *ask* ("I step on this block"), a single authority (the host or the game process) decides and tells everybody.
- **Fault tolerance:** heartbeat, pause when a player goes silent, 10 s grace period, rejoin with the same identity, host migration in P2P mode.
- **Consistency over availability (CAP):** on a partition the match pauses instead of diverging.

## Controls

| Key | Action |
|-----|--------|
| `W` `A` `S` `D` | Move |
| Mouse | Look around |
| `Space` | Jump |
| Left click | Dash |

## Requirements

- Python 3
- [Poetry](https://python-poetry.org/)
- [Redis](https://redis.io/) — **only for matchmaking mode** (the matchmaking server refuses to start without it)

```bash
poetry install
```

### Installing Redis

Redis runs on the machine that hosts the matchmaking server. Clients and P2P players do not need it.

Install [Docker](https://docs.docker.com/get-docker/), then start Redis in a container:

```bash
docker run -d --name redis -p 6379:6379 redis
```

Check that it answers:

```bash
docker exec redis redis-cli ping
```

You should see `PONG`. By default Redis listens on `localhost:6379`.

## How to play

### Local (P2P)

No server needed.

```bash
poetry run python main.py
```

Choose **Local → Host** on one PC and **Local → Join** on the others (same network). The host appears in the list within a few seconds.

### Matchmaking

On the machine that will act as server, start Redis first (see above), then the matchmaking server:

```bash
poetry run python server/matchmaking_server.py
```

Then start the game on each client and choose **Matchmaking**:

```bash
poetry run python main.py
```

When three players are in the queue, the server starts a `game_instance` process and tells everybody where to connect.

The address of the matchmaking server is set in `shared/config.py`.

## How it works

```
 Client A ─┐                         ┌─ asks:    block_step, player_ready, update_pos
 Client B ─┼──►  GameAuthority  ◄────┤
 Client C ─┘   (rules + state)       └─ decides: block_destroyed, state_snapshot, game_won
```

- **Authority:** `GameAuthority` holds the rules and the state of the match. In P2P it runs on the host; in matchmaking it runs in `server/game_instance.py`. The code is the same, only the way it is started changes.
- **Transport:** the join handshake is TCP (once); all the game traffic is UDP. A lost position packet is replaced by the next one.
- **Snapshots:** the authority sends the positions of all players every 25 ms (matchmaking) or 50 ms (P2P).
- **Redis:** used only as a registry of running matches (key `game:<port>`, TTL 3 s, renewed every second). If a game process dies, its key expires by itself, and a restarted matchmaking server adopts the matches that are still running.
- **Failures:** the host sends a heartbeat every second. After 5 s of silence the match is paused for everybody; the player has 10 s to come back with the same id, otherwise he is removed. If the P2P host disappears, the player with the lowest id becomes the new host.

### Project layout

```
shared/    network and rules, used by both sides (never touches the graphics)
server/    matchmaking_server.py, game_instance.py
tests/     automatic tests
```

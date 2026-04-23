# Rift-Records
When the time for Customs arrives....


# ⚔ Rift Records — Local League of Legends Custom Match Tracker

A Flask + SQLite web app for tracking in-house League of Legends custom games,
player stats, roles, win rates, and a live Elo-based MMR system.

---

## 📁 Project Structure

```
rift-records/
├── app.py               ← Flask backend (all API routes + MMR logic)
├── requirements.txt     ← Python dependencies
├── rift_records.db      ← SQLite database (auto-created on first run)
├── static/
│   └── index.html       ← Frontend (served by Flask)
└── README.md
```

---

## 🚀 Setup & Running

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the server

```bash
python app.py
```

### 3. Open in browser

- **Your machine:** http://localhost:5000
- **Others on your network:** http://<YOUR_LAN_IP>:5000
  - Find your IP with `ipconfig` (Windows) or `ifconfig` / `ip a` (Mac/Linux)

---
# Install ngrok from https://ngrok.com, then:
ngrok http 5000

## 🌐 API Reference

All endpoints return JSON.

### Players

| Method | Endpoint                  | Description              |
|--------|---------------------------|--------------------------|
| GET    | `/api/players`            | List all players (sorted by MMR) |
| POST   | `/api/players`            | Add a new player `{ "name": "..." }` |
| DELETE | `/api/players/<id>`       | Remove a player          |

### Matches

| Method | Endpoint                  | Description              |
|--------|---------------------------|--------------------------|
| GET    | `/api/matches`            | List all matches (newest first) |
| POST   | `/api/matches`            | Record a new match (see below) |
| DELETE | `/api/matches/<id>`       | Revert a match + undo MMR |

### Stats

| Method | Endpoint    | Description                  |
|--------|-------------|------------------------------|
| GET    | `/api/stats`| Global summary (totals, top player) |

### POST /api/matches — Body format

```json
{
  "winner": "blue",
  "blue": [
    { "player_id": 1, "role": "Top" },
    { "player_id": 2, "role": "Jungle" },
    { "player_id": 3, "role": "Mid" },
    { "player_id": 4, "role": "Bot" },
    { "player_id": 5, "role": "Support" }
  ],
  "red": [
    { "player_id": 6, "role": "Top" },
    ...
  ]
}
```

---

## ⚙️ Customising the MMR System

Open `app.py` and find the `calc_delta()` function (~line 45):

```python
def calc_delta(winner_avg_mmr: float, loser_avg_mmr: float) -> int:
    expected = 1 / (1 + math.pow(10, (loser_avg_mmr - winner_avg_mmr) / 400))
    K = 28          # ← increase for faster MMR swings, decrease for slower
    gain = round(K * (1 - expected))
    return max(10, min(50, gain))   # ← change clamp range [min, max]
```

**Tier thresholds** are in `get_tier()` just below — edit the MMR breakpoints freely.

**Starting MMR** defaults to 1200. Change the `DEFAULT 1200` in the `CREATE TABLE players` statement inside `init_db()`.

> ⚠️ If you change the schema after the DB has been created, delete `rift_records.db` and restart — it will be recreated automatically.

---

## 🗄️ Direct Database Access

```bash
sqlite3 rift_records.db
```

Useful queries:
```sql
SELECT name, mmr, wins, losses FROM players ORDER BY mmr DESC;
SELECT * FROM matches ORDER BY id DESC LIMIT 10;
SELECT p.name, rc.role, rc.count FROM role_counts rc JOIN players p ON p.id = rc.player_id;
```

---

## 🔒 Running on Your LAN (Multi-User)

The server already binds to `0.0.0.0` (all interfaces), so anyone on your
local network can reach it via your machine's IP. For a more permanent setup:

- **Windows:** Allow port 5000 through Windows Firewall
- **Mac/Linux:** No extra config needed in most cases
- For internet exposure (not recommended for casual use), look into ngrok or a reverse proxy

---

## 📦 Dependencies

- `flask` — web framework
- `flask-cors` — cross-origin support for LAN clients

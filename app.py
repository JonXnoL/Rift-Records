"""
Rift Records — Flask + SQLite Backend
Run: python app.py
Then open http://localhost:5000 in your browser (or share your LAN IP).
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import sqlite3
import os
import math
from datetime import datetime

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)  # Allow requests from any origin on your LAN

DB_PATH = os.path.join(os.path.dirname(__file__), "rift_records.db")

# ─────────────────────────────────────────────
#  DATABASE SETUP
# ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # allows dict-like access
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            mmr       INTEGER NOT NULL DEFAULT 1200,
            wins      INTEGER NOT NULL DEFAULT 0,
            losses    INTEGER NOT NULL DEFAULT 0,
            win_streak INTEGER NOT NULL DEFAULT 0,
            created_at TEXT   DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS role_counts (
            player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            role      TEXT    NOT NULL,
            count     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player_id, role)
        );

        CREATE TABLE IF NOT EXISTS matches (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            winner     TEXT    NOT NULL CHECK(winner IN ('blue','red')),
            mmr_delta  INTEGER NOT NULL,
            played_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS match_slots (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id   INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
            team       TEXT    NOT NULL CHECK(team IN ('blue','red')),
            role       TEXT    NOT NULL,
            player_id  INTEGER NOT NULL REFERENCES players(id)
        );
        """)


# ─────────────────────────────────────────────
#  MMR HELPERS
# ─────────────────────────────────────────────

# AFTER
TARGET_GAIN = 40  # normalized gain at average MMR

def calc_deltas(winner_slots, loser_slots, db) -> tuple[list, list]:
    """
    Curve-based MMR system.
    - Base gain normalized to TARGET_GAIN around average MMR
    - Players above average gain less / lose more
    - Players below average gain more / lose less
    - Elo team-strength modifier still applies on top
    """
    # Gather all player MMRs to find the pool average
    all_mmrs = [r["mmr"] for r in db.execute("SELECT mmr FROM players").fetchall()]
    pool_avg = sum(all_mmrs) / len(all_mmrs) if all_mmrs else 1200
    mmr_spread = max(300, (max(all_mmrs) - min(all_mmrs)) / 2) if len(all_mmrs) > 1 else 300

    def get_mmr(player_id):
        return db.execute("SELECT mmr FROM players WHERE id=?", (player_id,)).fetchone()["mmr"]

    winner_mmrs = [get_mmr(s["player_id"]) for s in winner_slots]
    loser_mmrs  = [get_mmr(s["player_id"]) for s in loser_slots]
    winner_avg  = sum(winner_mmrs) / len(winner_mmrs)
    loser_avg   = sum(loser_mmrs)  / len(loser_mmrs)

    # Elo expected score — accounts for team skill difference
    expected = 1 / (1 + math.pow(10, (loser_avg - winner_avg) / 400))
    elo_modifier = (1 - expected)  # >0.5 if underdog wins, <0.5 if favourite wins

    def curve_multiplier(player_mmr):
        """
        Returns a multiplier < 1 if above average (gains less, loses more)
        and > 1 if below average (gains more, loses less).
        Multiplier is 1.0 exactly at pool average.
        Uses a smooth sigmoid-like curve, clamped to [0.5, 1.8].
        """
        deviation = (player_mmr - pool_avg) / mmr_spread  # normalised -1..+1 roughly
        multiplier = 1.0 - (0.5 * deviation)              # linear curve around 1.0
        return max(0.5, min(1.8, multiplier))

    winner_deltas = []
    loser_deltas  = []

    for mmr in winner_mmrs:
        mult  = curve_multiplier(mmr)
        delta = round(TARGET_GAIN * mult * elo_modifier * 2)  # *2 because elo_modifier ~0.5 at even
        winner_deltas.append(max(8, min(70, delta)))

    for mmr in loser_mmrs:
        mult  = curve_multiplier(mmr)
        # Losses are the mirror — above average loses more, below average loses less
        loss_mult = 2.0 - mult   # inverts the curve: high mult player loses less, low mult loses more... wait, flip:
        # Actually: above-avg player (mult < 1) should lose MORE → use (2 - mult) which is > 1
        # Below-avg player (mult > 1) should lose LESS → (2 - mult) which is < 1. Correct.
        delta = round(TARGET_GAIN * (2.0 - mult) * (1 - elo_modifier) * 2)
        loser_deltas.append(max(8, min(70, delta)))

    return winner_deltas, loser_deltas


def get_tier(mmr: int) -> dict:
    tiers = [
        (900,  "DSHU Lo",     "iron"),
        (1100, "Jinx Smells Advocate",   "bronze"),
        (1300, "Midtown Climber",   "silver"),
        (1500, "Marietta Dweller",     "gold"),
        (1700, "Mellow Mushroom Employee", "plat"),
        (1900, "Tennis Enjoyer",  "emerald"),
        (2100, "QT Abuser",  "diamond"),
    ]
    for threshold, label, cls in tiers:
        if mmr < threshold:
            return {"label": label, "cls": f"tier-{cls}"}
    return {"label": "Bonfire Attendee", "cls": "tier-masters"}


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def player_to_dict(row, db) -> dict:
    roles = {}
    for r in db.execute(
        "SELECT role, count FROM role_counts WHERE player_id = ?", (row["id"],)
    ):
        roles[r["role"]] = r["count"]

    mmr = row["mmr"]
    wins = row["wins"]
    losses = row["losses"]
    games = wins + losses
    wr = round((wins / games) * 100) if games > 0 else 0

    fav_role = max(roles, key=roles.get) if roles else None

    return {
        "id":        row["id"],
        "name":      row["name"],
        "mmr":       mmr,
        "wins":      wins,
        "losses":    losses,
        "games":     games,
        "winrate":   wr,
        "roles":     roles,
        "fav_role":  fav_role,
        "tier":      get_tier(mmr),
        "win_streak": row["win_streak"],
        "created_at": row["created_at"],
    }


# ─────────────────────────────────────────────
#  ROUTES — STATIC FRONTEND
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ─────────────────────────────────────────────
#  ROUTES — PLAYERS
# ─────────────────────────────────────────────

@app.route("/api/players", methods=["GET"])
def get_players():
    with get_db() as db:
        rows = db.execute("SELECT * FROM players ORDER BY mmr DESC").fetchall()
        return jsonify([player_to_dict(r, db) for r in rows])


@app.route("/api/players", methods=["POST"])
def create_player():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    if len(name) > 32:
        return jsonify({"error": "Name must be 32 characters or fewer"}), 400

    # Optional starting MMR — defaults to 1200 if not provided
    starting_mmr = data.get("mmr")
    if starting_mmr is not None:
        try:
            starting_mmr = int(starting_mmr)
        except (TypeError, ValueError):
            return jsonify({"error": "MMR must be a whole number"}), 400
        if not (100 <= starting_mmr <= 9999):
            return jsonify({"error": "MMR must be between 100 and 9999"}), 400
    else:
        starting_mmr = 1200

    with get_db() as db:
        try:
            cur = db.execute(
                "INSERT INTO players (name, mmr) VALUES (?, ?)", (name, starting_mmr)
            )
            db.commit()
            row = db.execute("SELECT * FROM players WHERE id = ?", (cur.lastrowid,)).fetchone()
            return jsonify(player_to_dict(row, db)), 201
        except sqlite3.IntegrityError:
            return jsonify({"error": "Player already exists"}), 409

@app.route("/api/players/<int:player_id>", methods=["DELETE"])
def delete_player(player_id):
    with get_db() as db:
        row = db.execute("SELECT id FROM players WHERE id = ?", (player_id,)).fetchone()
        if not row:
            return jsonify({"error": "Player not found"}), 404

        # Remove this player from all match slots, then clean up
        # orphaned matches (matches where one team now has fewer than 5 players)
        db.execute("DELETE FROM role_counts WHERE player_id = ?", (player_id,))
        db.execute("DELETE FROM match_slots WHERE player_id = ?", (player_id,))

        # Delete matches that are now incomplete (lost a player)
        db.execute("""
            DELETE FROM matches WHERE id IN (
                SELECT m.id FROM matches m
                LEFT JOIN match_slots ms ON ms.match_id = m.id
                GROUP BY m.id
                HAVING COUNT(ms.id) < 10
            )
        """)

        db.execute("DELETE FROM players WHERE id = ?", (player_id,))
        db.commit()
        return jsonify({"deleted": player_id})

# ─────────────────────────────────────────────
#  ROUTES — MATCHES
# ─────────────────────────────────────────────

@app.route("/api/matches", methods=["GET"])
def get_matches():
    with get_db() as db:
        matches = db.execute(
            "SELECT * FROM matches ORDER BY id DESC"
        ).fetchall()

        result = []
        for m in matches:
            slots = db.execute("""
                SELECT ms.team, ms.role, p.name, p.id as player_id
                FROM match_slots ms
                JOIN players p ON p.id = ms.player_id
                WHERE ms.match_id = ?
            """, (m["id"],)).fetchall()

            blue = [{"player": s["name"], "player_id": s["player_id"], "role": s["role"]}
                    for s in slots if s["team"] == "blue"]
            red  = [{"player": s["name"], "player_id": s["player_id"], "role": s["role"]}
                    for s in slots if s["team"] == "red"]

            # Build MMR change labels from stored delta
            delta = m["mmr_delta"]
            mmr_changes = (
                [{"player": s["player"], "delta": +delta} for s in blue if m["winner"] == "blue"] +
                [{"player": s["player"], "delta": -delta} for s in blue if m["winner"] == "red"] +
                [{"player": s["player"], "delta": +delta} for s in red  if m["winner"] == "red"] +
                [{"player": s["player"], "delta": -delta} for s in red  if m["winner"] == "blue"]
            )

            result.append({
                "id":         m["id"],
                "winner":     m["winner"],
                "mmr_delta":  delta,
                "played_at":  m["played_at"],
                "blue":       blue,
                "red":        red,
                "mmr_changes": mmr_changes,
            })

        return jsonify(result)


@app.route("/api/matches", methods=["POST"])
def create_match():
    data = request.get_json()
    winner = data.get("winner")          # 'blue' or 'red'
    blue_slots = data.get("blue", [])    # [{player_id, role}, ...]
    red_slots  = data.get("red",  [])

    if winner not in ("blue", "red"):
        return jsonify({"error": "winner must be 'blue' or 'red'"}), 400
    if len(blue_slots) != 5 or len(red_slots) != 5:
        return jsonify({"error": "Each team must have exactly 5 players"}), 400

    all_ids = [s["player_id"] for s in blue_slots + red_slots]
    if len(set(all_ids)) != 10:
        return jsonify({"error": "All 10 players must be unique"}), 400

    with get_db() as db:
        # Fetch current MMRs
        def avg_mmr(slots):
            total = 0
            for s in slots:
                row = db.execute("SELECT mmr FROM players WHERE id = ?", (s["player_id"],)).fetchone()
                if not row:
                    return None
                total += row["mmr"]
            return total / len(slots)

        blue_avg = avg_mmr(blue_slots)
        red_avg  = avg_mmr(red_slots)
        if blue_avg is None or red_avg is None:
            return jsonify({"error": "One or more players not found"}), 404

        # AFTER — individual deltas, streak tracking
        winner_slots_data = blue_slots if winner == "blue" else red_slots
        loser_slots_data  = red_slots  if winner == "blue" else blue_slots

        winner_deltas, loser_deltas = calc_deltas(winner_slots_data, loser_slots_data, db)

        # Store average delta for the match record (for display purposes)
        avg_delta = round((sum(winner_deltas) + sum(loser_deltas)) / 10)

        cur = db.execute(
            "INSERT INTO matches (winner, mmr_delta) VALUES (?, ?)",
            (winner, avg_delta)
        )
        match_id = cur.lastrowid

        # Update winners
        for i, s in enumerate(winner_slots_data):
            team = "blue" if winner == "blue" else "red"
            db.execute(
                "INSERT INTO match_slots (match_id, team, role, player_id) VALUES (?,?,?,?)",
                (match_id, team, s["role"], s["player_id"])
            )
            delta = winner_deltas[i]
            db.execute(
                "UPDATE players SET wins=wins+1, mmr=MAX(100,mmr+?), win_streak=win_streak+1 WHERE id=?",
                (delta, s["player_id"])
            )
            db.execute("""
                INSERT INTO role_counts (player_id, role, count) VALUES (?,?,1)
                ON CONFLICT(player_id, role) DO UPDATE SET count = count + 1
            """, (s["player_id"], s["role"]))

        # Update losers
        for i, s in enumerate(loser_slots_data):
            team = "red" if winner == "blue" else "blue"
            db.execute(
                "INSERT INTO match_slots (match_id, team, role, player_id) VALUES (?,?,?,?)",
                (match_id, team, s["role"], s["player_id"])
            )
            delta = loser_deltas[i]
            db.execute(
                "UPDATE players SET losses=losses+1, mmr=MAX(100,mmr-?), win_streak=0 WHERE id=?",
                (delta, s["player_id"])
            )
            db.execute("""
                INSERT INTO role_counts (player_id, role, count) VALUES (?,?,1)
                ON CONFLICT(player_id, role) DO UPDATE SET count = count + 1
            """, (s["player_id"], s["role"]))

        db.commit()

        return jsonify({
            "match_id":  match_id,
            "mmr_delta": avg_delta,
            "winner":    winner,
        }), 201

@app.route("/api/matches/<int:match_id>", methods=["DELETE"])
def delete_match(match_id):
    """Revert a match: undo MMR changes and delete record."""
    with get_db() as db:
        match = db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not match:
            return jsonify({"error": "Match not found"}), 404

        delta = match["mmr_delta"]
        winner = match["winner"]

        slots = db.execute(
            "SELECT * FROM match_slots WHERE match_id = ?", (match_id,)
        ).fetchall()


        for s in slots:
            won = s["team"] == winner
            db.execute(
                "UPDATE players SET wins=wins-?, losses=losses-?, mmr=MAX(100,mmr-?), win_streak=0 WHERE id=?",
                (1 if won else 0, 0 if won else 1, delta if won else -delta, s["player_id"])
            )
            db.execute("""
                UPDATE role_counts SET count = MAX(0, count - 1)
                WHERE player_id = ? AND role = ?
            """, (s["player_id"], s["role"]))

        db.execute("DELETE FROM matches WHERE id = ?", (match_id,))
        db.commit()
        return jsonify({"deleted": match_id, "mmr_reverted": True})
# ─────────────────────────────────────────────
#  ROUTES — HISTORY
# ─────────────────────────────────────────────
@app.route("/api/mmr-history", methods=["GET"])
def get_mmr_history():
    """
    Returns each player's MMR at the end of each of their last 20 matches,
    reconstructed by walking match history in chronological order.
    """
    with get_db() as db:
        players = db.execute("SELECT id, name, mmr FROM players ORDER BY mmr DESC").fetchall()

        # Fetch last 20 matches in chronological order
        matches = db.execute("""
            SELECT id, winner, mmr_delta, played_at FROM matches
            ORDER BY id DESC LIMIT 20
        """).fetchall()
        matches = list(reversed(matches))  # oldest first for left-to-right chart

        # For each player, reconstruct their MMR at each match point
        # We do this by starting from current MMR and walking backwards,
        # then reversing to get chronological order.
        result = {}
        for p in players:
            player_id = p["id"]
            current_mmr = p["mmr"]
            history = []  # will build backwards then reverse

            for m in reversed(matches):
                slot = db.execute("""
                    SELECT team FROM match_slots
                    WHERE match_id = ? AND player_id = ?
                """, (m["id"], player_id)).fetchone()

                if slot is None:
                    continue  # player not in this match

                # Determine if they won
                won = slot["team"] == m["winner"]
                delta = m["mmr_delta"]

                # current_mmr is their MMR AFTER this match
                # so their MMR before was current_mmr ∓ delta
                mmr_after  = current_mmr
                mmr_before = current_mmr - (delta if won else -delta)
                mmr_before = max(100, mmr_before)

                history.append({
                    "match_id":  m["id"],
                    "played_at": m["played_at"],
                    "mmr":       mmr_after,
                    "won":       won,
                    "delta":     delta if won else -delta,
                })

                current_mmr = mmr_before  # step back further

            history.reverse()  # now chronological

            if history:
                result[str(player_id)] = {
                    "name":    p["name"],
                    "history": history,
                }

        return jsonify(result)
# ─────────────────────────────────────────────
#  ROUTES — STATS / MISC
# ─────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def get_stats():
    with get_db() as db:
        total_matches = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        total_players = db.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        top_player = db.execute(
            "SELECT name, mmr FROM players ORDER BY mmr DESC LIMIT 1"
        ).fetchone()
        return jsonify({
            "total_matches": total_matches,
            "total_players": total_players,
            "top_player": dict(top_player) if top_player else None,
        })


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("\n  🗡  Rift Records server starting...")
    print("  → Local:   http://localhost:5000")
    print("  → Network: http://<your-ip>:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=True)

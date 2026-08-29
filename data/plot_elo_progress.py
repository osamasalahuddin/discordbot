import json
import sys
import argparse
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

LADDER_PATH = r"E:\Work\Claude\data\unranked_ladder.json"
OUT_DIR = r"E:\Work\Claude\data\graphs"


def plot_player(player_name, ladder_data, out_dir=OUT_DIR):
    players = ladder_data["players"]
    if player_name not in players:
        available = ", ".join(players.keys())
        raise ValueError(f"Unknown player '{player_name}'. Available: {available}")

    p = players[player_name]
    history = p["history"]
    if not history:
        print(f"{player_name}: no match history, skipping plot.")
        return None

    dates = [datetime.fromisoformat(h["date"]) for h in history]
    elos = [h["elo_after"] for h in history]
    starting_elo = p["starting_elo"]

    plot_dates = [dates[0]] + dates
    plot_elos = [starting_elo] + elos

    win_mask = [h["won"] for h in history]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(plot_dates, plot_elos, color="#3b6ea5", linewidth=1.4, zorder=2)

    win_dates = [d for d, w in zip(dates, win_mask) if w]
    win_elos = [e for e, w in zip(elos, win_mask) if w]
    loss_dates = [d for d, w in zip(dates, win_mask) if not w]
    loss_elos = [e for e, w in zip(elos, win_mask) if not w]

    ax.scatter(win_dates, win_elos, color="#2e8b57", s=10, label="Win", zorder=3)
    ax.scatter(loss_dates, loss_elos, color="#c0392b", s=10, label="Loss", zorder=3)

    ax.axhline(starting_elo, color="gray", linestyle="--", linewidth=0.8, alpha=0.6, label=f"Starting Elo ({starting_elo})")

    ax.set_title(f"{player_name} — Unranked Group Elo Progress ({len(history)} matches)", fontsize=13)
    ax.set_xlabel("Date")
    ax.set_ylabel("Elo")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)

    fig.autofmt_xdate()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    final_elo = p["current_elo"]
    ax.annotate(
        f"Final: {final_elo}",
        xy=(dates[-1], elos[-1]),
        xytext=(10, 10), textcoords="offset points",
        fontsize=9, fontweight="bold",
    )

    fig.tight_layout()

    safe_name = player_name.replace(" ", "_").replace("&", "and").replace(".", "")
    out_path = rf"{out_dir}\elo_{safe_name}.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"{player_name}: wrote {out_path} ({len(history)} matches, final Elo {final_elo})")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Plot a tracked player's Elo progress over the unranked group ladder.")
    parser.add_argument("player", nargs="?", help="Player name (must match a key in unranked_ladder.json). If omitted, plots all players.")
    parser.add_argument("--ladder", default=LADDER_PATH, help="Path to unranked_ladder.json")
    parser.add_argument("--out", default=OUT_DIR, help="Output directory for PNGs")
    args = parser.parse_args()

    import os
    os.makedirs(args.out, exist_ok=True)

    with open(args.ladder, encoding="utf-8") as f:
        ladder_data = json.load(f)

    if args.player:
        plot_player(args.player, ladder_data, args.out)
    else:
        for name in ladder_data["players"]:
            plot_player(name, ladder_data, args.out)


if __name__ == "__main__":
    main()

import os
import sys
import io
import asyncio
import json
from collections import namedtuple
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from team_balancer import balance_teams

# Defaults assume the repo layout (data/ is a sibling of code/). Override with the
# DATA_DIR env var, or point at the files directly with LADDER_PATH / MAP_ELO_PATH.
# Resolves from the script location, so it works regardless of the launch cwd.
DATA_DIR = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
LADDER_PATH = os.environ.get("LADDER_PATH", os.path.join(DATA_DIR, "unranked_ladder.json"))
MAP_ELO_PATH = os.environ.get("MAP_ELO_PATH", os.path.join(DATA_DIR, "map_elo.json"))
OPENCLOSED_PATH = os.environ.get("OPENCLOSED_PATH", os.path.join(DATA_DIR, "openclosed_elo.json"))

# A player needs at least this many games in a scope before that scope's Elo is
# trusted. Below the threshold the bot falls back to their overall ladder Elo.
FALLBACK_MIN_GAMES = 5

# Open/closed membership comes from the pipeline's single source of truth so the
# bot and the ladder can't disagree about what counts as a closed map.
if DATA_DIR not in sys.path:
    sys.path.insert(0, DATA_DIR)
try:
    from map_types import OPEN_MAPS, CLOSED_MAPS
except ImportError:          # data/ not alongside the bot - degrade gracefully
    OPEN_MAPS, CLOSED_MAPS = set(), set()

SCOPE_ALL = "All maps"
SCOPE_OPEN = "Open maps"
SCOPE_CLOSED = "Closed maps"
SPECIAL_SCOPES = (SCOPE_ALL, SCOPE_OPEN, SCOPE_CLOSED)


def load_ladder():
    with open(LADDER_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_players():
    return {name: p["current_elo"] for name, p in load_ladder()["players"].items()}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_map_elo():
    return _load(MAP_ELO_PATH, {})


def load_openclosed():
    return _load(OPENCLOSED_PATH, {}).get("results", {})


def map_names_by_popularity():
    """All maps with Elo data, most-played first. 'Unknown' is dropped."""
    maps = [(n, v.get("total_matches", 0)) for n, v in load_map_elo().items() if n != "Unknown"]
    maps.sort(key=lambda kv: -kv[1])
    return [n for n, _ in maps]


# A Scope is any pool of Elo the bot can rate players against: the overall
# ladder, the open/closed sub-ladders, or one specific map. They all share the
# same per-player shape, so every command can treat them identically.
#   pool -- {player: {current_elo, matches_played, wins, losses, win_rate}},
#           or None for the overall ladder (which never needs a fallback).
Scope = namedtuple("Scope", "label pool total is_overall")


def resolve_scope(value):
    """Turn a user-supplied scope string into a Scope. None if unrecognised.

    Accepts None / "All maps" (overall), "Open maps", "Closed maps", or a map
    name, case-insensitively.
    """
    if not value or value.strip().lower() == SCOPE_ALL.lower():
        ladder = load_ladder()
        total = ladder["scrape_meta"].get("qualifying_matches_used", 0)
        return Scope(SCOPE_ALL, None, total, True)

    v = value.strip().lower()
    for label, key in ((SCOPE_OPEN, "open"), (SCOPE_CLOSED, "closed")):
        if v in (label.lower(), key):
            bucket = load_openclosed().get(key)
            if not bucket:
                return None
            return Scope(label, bucket.get("players", {}), bucket.get("total_matches", 0), False)

    map_elo = load_map_elo()
    match = next((k for k in map_elo if k.lower() == v), None)
    if match is None:
        return None
    bucket = map_elo[match]
    return Scope(match, bucket.get("players", {}), bucket.get("total_matches", 0), False)


def scope_maps(scope):
    """Set of map names a scope covers, or None when it covers everything."""
    if scope.is_overall:
        return None
    if scope.label == SCOPE_OPEN:
        return OPEN_MAPS
    if scope.label == SCOPE_CLOSED:
        return CLOSED_MAPS
    return {scope.label}


def scoped_elo(name, overall_elo, scope):
    """(elo, used_scope_elo, games_in_scope) for a player within a scope.

    Uses the scope's own Elo only when the player has >= FALLBACK_MIN_GAMES
    there; otherwise falls back to their overall ladder Elo. The overall scope
    is always "used" and reports no game count.
    """
    if scope.is_overall or scope.pool is None:
        return overall_elo, True, None
    entry = scope.pool.get(name) or {}
    games = entry.get("matches_played", 0)
    if games >= FALLBACK_MIN_GAMES:
        return entry["current_elo"], True, games
    return overall_elo, False, games


def build_embed(result, scope, fallback_names=None):
    fallback_names = set(fallback_names or [])

    def fmt(team):
        return "\n".join(
            f"- {name} ({int(round(elo))}){' *' if name in fallback_names else ''}"
            for name, elo in team
        )

    team_a_label = "Team A" + (" 🤖" if result["ai_team"] == "A" else "")
    team_b_label = "Team B" + (" 🤖" if result["ai_team"] == "B" else "")

    embed = discord.Embed(title=f"Balanced Teams — {scope.label}", color=0x3B6EA5)
    embed.add_field(name=f"{team_a_label} — avg {result['avg_a']:.0f}", value=fmt(result["team_a"]), inline=True)
    embed.add_field(name=f"{team_b_label} — avg {result['avg_b']:.0f}", value=fmt(result["team_b"]), inline=True)

    footer = f"Elo gap: {result['elo_diff']:.0f}"
    if result["ai_added"]:
        footer += " | AI added to even out team size"
    footer += f" | using {scope.label} Elo"
    if fallback_names:
        footer += f" | * overall Elo (<{FALLBACK_MIN_GAMES} games in {scope.label})"
    embed.set_footer(text=footer)
    return embed


class PlayerSelect(discord.ui.Select):
    def __init__(self, players, scope):
        self.players = players
        self.scope = scope
        options = [
            discord.SelectOption(label=f"{name} (Elo {int(round(elo))})", value=name)
            for name, elo in sorted(players.items(), key=lambda kv: -kv[1])
        ]
        super().__init__(
            placeholder="Choose players for this match...",
            min_values=2,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen, fallback_names = [], []
        for name in self.values:
            elo, used, _ = scoped_elo(name, self.players[name], self.scope)
            chosen.append((name, elo))
            if not used:
                fallback_names.append(name)

        result = balance_teams(chosen)
        embed = build_embed(result, self.scope, fallback_names)
        # The selection prompt is ephemeral (only the caller picks players); post
        # the balanced teams as a new public message so the whole channel sees them.
        await interaction.response.edit_message(content="Teams generated ✅", view=None)
        await interaction.channel.send(embed=embed)


class PlayerSelectView(discord.ui.View):
    def __init__(self, players, scope):
        super().__init__(timeout=120)
        self.add_item(PlayerSelect(players, scope))


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


async def scope_autocomplete(interaction: discord.Interaction, current: str):
    """Offers All / Open / Closed first, then individual maps by popularity."""
    current = current.lower()
    names = list(SPECIAL_SCOPES) + map_names_by_popularity()
    matches = [n for n in names if current in n.lower()][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


async def player_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    ordered = sorted(load_players().items(), key=lambda kv: -kv[1])
    matches = [name for name, _ in ordered if current in name.lower()][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


async def _reject_scope(interaction, value):
    await interaction.response.send_message(
        f"**{value}** isn't a known scope. Pick one from the list: "
        f"{', '.join(SPECIAL_SCOPES)}, or a map name.",
        ephemeral=True,
    )


@bot.tree.command(name="balance", description="Pick players and get balanced teams")
@app_commands.describe(scope="All maps (default), Open maps, Closed maps, or one specific map")
@app_commands.autocomplete(scope=scope_autocomplete)
async def balance_cmd(interaction: discord.Interaction, scope: str | None = None):
    sc = resolve_scope(scope)
    if sc is None:
        await _reject_scope(interaction, scope)
        return

    view = PlayerSelectView(load_players(), sc)
    await interaction.response.send_message(
        f"Select the players for this **{sc.label}** match:", view=view, ephemeral=True
    )


@bot.tree.command(name="players", description="List tracked players and their Elo")
@app_commands.describe(scope="All maps (default), Open maps, Closed maps, or one specific map")
@app_commands.autocomplete(scope=scope_autocomplete)
async def players_cmd(interaction: discord.Interaction, scope: str | None = None):
    sc = resolve_scope(scope)
    if sc is None:
        await _reject_scope(interaction, scope)
        return

    rows = []
    for name, overall in load_players().items():
        elo, used, games = scoped_elo(name, overall, sc)
        rows.append((name, elo, used, games))
    rows.sort(key=lambda r: -r[1])

    if sc.is_overall:
        lines = [f"**{n}** — {int(round(e))}" for n, e, _, _ in rows]
        footer = f"{sc.total} matches on the ladder"
    else:
        lines = [
            f"**{n}** — {int(round(e))}  ({g} games){'' if u else ' *'}"
            for n, e, u, g in rows
        ]
        footer = f"{sc.total} matches in {sc.label}"
        if any(not u for _, _, u, _ in rows):
            footer += f" | * overall Elo (<{FALLBACK_MIN_GAMES} games here)"

    embed = discord.Embed(
        title=f"Tracked Players — {sc.label}",
        description="\n".join(lines),
        color=0x3B6EA5,
    )
    embed.set_footer(text=footer)
    await interaction.response.send_message(embed=embed)


def _civ_counts(ladder, user_path, maps=None):
    """Civ usage for a player, optionally restricted to a set of map names."""
    counts = {}
    if not user_path:
        return counts
    for m in ladder.get("match_log", []):
        if maps is not None and m.get("map") not in maps:
            continue
        for team in m["teams"]:
            for p in team["players"]:
                if p.get("user_path") == user_path:
                    counts[p["civ"]] = counts.get(p["civ"], 0) + 1
    return counts


@bot.tree.command(name="playerstats", description="Show a player's stats overall or in one scope")
@app_commands.describe(
    player="Tracked player",
    scope="All maps (default), Open maps, Closed maps, or one specific map",
)
@app_commands.autocomplete(player=player_autocomplete, scope=scope_autocomplete)
async def playerstats_cmd(interaction: discord.Interaction, player: str, scope: str | None = None):
    ladder = load_ladder()
    players = ladder["players"]

    pname = next((k for k in players if k.lower() == player.lower()), None)
    if pname is None:
        await interaction.response.send_message(
            f"**{player}** isn't a tracked player. Try `/players`.", ephemeral=True
        )
        return

    sc = resolve_scope(scope)
    if sc is None:
        await _reject_scope(interaction, scope)
        return

    entry = players[pname]
    overall_elo = entry["current_elo"]
    user_path = entry.get("user_path")
    embed = discord.Embed(title=f"{pname} — {sc.label}", color=0x3B6EA5)

    # ---------- overall ladder ----------
    if sc.is_overall:
        ranked = sorted(((n, p["current_elo"]) for n, p in players.items()), key=lambda kv: -kv[1])
        rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == pname), None)
        hist = entry.get("history", [])
        form = " ".join("✅" if h["won"] else "❌" for h in hist[-5:][::-1]) or "—"

        played = []
        for mname, bucket in load_map_elo().items():
            if mname == "Unknown":
                continue
            e = bucket.get("players", {}).get(pname) or {}
            if e.get("matches_played", 0) >= FALLBACK_MIN_GAMES:
                played.append((e["current_elo"], mname, e["matches_played"]))
        played.sort(reverse=True)
        best = " · ".join(f"{m} {int(round(v))} ({g}g)" for v, m, g in played[:3]) or "—"
        worst = " · ".join(f"{m} {int(round(v))} ({g}g)" for v, m, g in played[-3:][::-1]) or "—"

        civs = sorted(_civ_counts(ladder, user_path).items(), key=lambda kv: -kv[1])[:3]

        embed.add_field(
            name="Elo",
            value=f"**{int(round(overall_elo))}**  (#{rank} of {len(ranked)})",
            inline=True,
        )
        embed.add_field(
            name="Record",
            value=f"**{entry['wins']}–{entry['losses']}**  ({entry['win_rate']:.0f}% win rate)\n"
                  f"{entry['matches_played']} games",
            inline=True,
        )
        embed.add_field(name="Recent form", value=form, inline=False)
        embed.add_field(name="Strongest maps", value=best, inline=False)
        embed.add_field(name="Weakest maps", value=worst, inline=False)
        embed.add_field(
            name="Most-played civs",
            value=", ".join(f"{c.title()} ({n})" for c, n in civs) or "—",
            inline=False,
        )
        if hist:
            embed.set_footer(
                text=f"First played {(hist[0].get('date') or '')[:10]} · "
                     f"last played {(hist[-1].get('date') or '')[:10]} | "
                     f"maps ranked with >= {FALLBACK_MIN_GAMES} games"
            )
        await interaction.response.send_message(embed=embed)
        return

    # ---------- one map, or open/closed ----------
    pool_entry = (sc.pool or {}).get(pname) or {}
    games = pool_entry.get("matches_played", 0)
    balance_elo, uses_scope, _ = scoped_elo(pname, overall_elo, sc)

    if not games:
        embed.description = (
            f"No recorded games in **{sc.label}**.\n"
            f"Used for balancing: **{int(round(overall_elo))}** (overall Elo)"
        )
        embed.set_footer(text=f"{sc.total} tracked matches in {sc.label}")
        await interaction.response.send_message(embed=embed)
        return

    scope_elo = pool_entry.get("current_elo", overall_elo)
    ranked = sorted(
        ((n, e["current_elo"]) for n, e in (sc.pool or {}).items() if e.get("matches_played", 0) > 0),
        key=lambda kv: -kv[1],
    )
    rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == pname), None)
    rank_str = f"  (#{rank} of {len(ranked)})" if rank else ""

    # History is stored per match with its map, so it can be filtered to any
    # scope - a single map, or every map in the open/closed bucket.
    smaps = scope_maps(sc)
    hist = [h for h in entry.get("history", []) if h.get("map") in smaps]
    if hist:
        form = " ".join("✅" if h["won"] else "❌" for h in hist[-5:][::-1])
        net = sum(h.get("delta", 0.0) for h in hist)
        form_value = f"{form}\nNet Elo here: {net:+.0f}"
    else:
        form_value = "—"

    # /balance and /players don't trust a thin sample: below FALLBACK_MIN_GAMES
    # they use the player's overall Elo. Show both so this view and the balancer
    # never appear to disagree about the same player in the same scope.
    elo_value = f"**{int(round(scope_elo))}**{rank_str}\nOverall: {int(round(overall_elo))}"
    if not uses_scope:
        elo_value += f"\nUsed for balancing: **{int(round(balance_elo))}** (overall)"

    embed.add_field(name="Elo here", value=elo_value, inline=True)
    embed.add_field(
        name="Record",
        value=f"**{pool_entry.get('wins', 0)}–{pool_entry.get('losses', 0)}**  "
              f"({pool_entry.get('win_rate', 0.0):.0f}% win rate)\n{games} games",
        inline=True,
    )
    embed.add_field(name="Recent form", value=form_value, inline=False)

    civs = sorted(_civ_counts(ladder, user_path, smaps).items(), key=lambda kv: -kv[1])[:3]
    embed.add_field(
        name="Most-played civs",
        value=", ".join(f"{c.title()} ({n})" for c, n in civs) or "—",
        inline=False,
    )

    footer = f"{sc.total} tracked matches in {sc.label}"
    if hist:
        footer = (f"First played {(hist[0].get('date') or '')[:10]} · "
                  f"last played {(hist[-1].get('date') or '')[:10]}")
    if not uses_scope:
        footer += f" | under {FALLBACK_MIN_GAMES} games here, so /balance uses overall Elo"
    embed.set_footer(text=footer)

    await interaction.response.send_message(embed=embed)


def scope_history(ladder, name, scope):
    """A player's dated Elo progression within a scope.

    Every scope stores the same shape: [{date, elo_after, won}, ...]. The
    overall ladder keeps it in unranked_ladder.json; map_elo.json and
    openclosed_elo.json each carry their own, so a per-map or open/closed graph
    plots that ladder's real progression rather than the overall one filtered.
    """
    if scope.is_overall:
        return ladder["players"].get(name, {}).get("history", [])
    return ((scope.pool or {}).get(name) or {}).get("history", [])


# Distinct enough to tell nine lines apart, and readable on Discord's dark theme.
PLAYER_COLOURS = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1",
    "#76b7b2", "#edc948", "#ff9da7", "#9c755f",
]


def _weekly(points):
    """Keep the last rating of each calendar week.

    Nine players at per-match resolution over two years is an unreadable
    hairball; one point per week keeps the trend and the final value while
    making the lines legible.
    """
    buckets = {}
    for d, e, w in points:                      # points are already sorted
        iso = d.isocalendar()
        buckets[(iso[0], iso[1])] = (d, e, w)
    return [buckets[k] for k in sorted(buckets)]


def render_elo_chart(series, title, footnote):
    """series: [(name, [(datetime, elo, won), ...]), ...] -> PNG bytes.

    One player gets every match plus win/loss dots; a multi-player comparison is
    resampled weekly so the lines stay readable. Agg backend, worker thread.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(11, 5.5))
    single = len(series) == 1

    for i, (name, points) in enumerate(series):
        if not points:
            continue
        if not single:
            points = _weekly(points)
        dates = [p[0] for p in points]
        elos = [p[1] for p in points]
        colour = PLAYER_COLOURS[i % len(PLAYER_COLOURS)]
        ax.plot(dates, elos, linewidth=1.5 if single else 1.6,
                color="#3b6ea5" if single else colour,
                label=None if single else f"{name} ({int(round(elos[-1]))})", zorder=2)
        if single:
            wins = [(d, e) for d, e, w in points if w]
            losses = [(d, e) for d, e, w in points if not w]
            if wins:
                ax.scatter([d for d, _ in wins], [e for _, e in wins],
                           s=11, color="#2e8b57", label="Win", zorder=3)
            if losses:
                ax.scatter([d for d, _ in losses], [e for _, e in losses],
                           s=11, color="#c0392b", label="Loss", zorder=3)
            ax.annotate(f"Final: {elos[-1]:.1f}", xy=(dates[-1], elos[-1]),
                        xytext=(8, 8), textcoords="offset points",
                        fontsize=9, fontweight="bold")

    ax.axhline(1000, color="gray", linestyle="--", linewidth=0.8, alpha=0.6,
               label="Starting Elo (1000)")
    if single:
        ax.margins(x=0.10)          # room for the "Final:" annotation
    ax.set_title(title, fontsize=13)
    ax.set_xlabel("Date")
    ax.set_ylabel("Elo")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8, ncol=2 if not single else 1)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    if footnote:
        fig.text(0.01, 0.01, footnote, fontsize=7.5, color="#666666")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return buf


@bot.tree.command(name="graph", description="Elo progression chart for one player or everyone")
@app_commands.describe(
    player="Leave empty to compare all tracked players",
    scope="All maps (default), Open maps, Closed maps, or one specific map",
)
@app_commands.autocomplete(player=player_autocomplete, scope=scope_autocomplete)
async def graph_cmd(interaction: discord.Interaction, player: str | None = None,
                    scope: str | None = None):
    ladder = load_ladder()
    players = ladder["players"]

    pname = None
    if player:
        pname = next((k for k in players if k.lower() == player.lower()), None)
        if pname is None:
            await interaction.response.send_message(
                f"**{player}** isn't a tracked player. Try `/players`.", ephemeral=True
            )
            return

    sc = resolve_scope(scope)
    if sc is None:
        await _reject_scope(interaction, scope)
        return

    wanted = [pname] if pname else list(players.keys())
    series = []
    for name in wanted:
        pts = []
        for h in scope_history(ladder, name, sc):
            if not h.get("date"):
                continue
            pts.append((datetime.fromisoformat(h["date"]), h["elo_after"], h["won"]))
        pts.sort(key=lambda t: t[0])
        if pts:
            series.append((name, pts))

    if not series:
        await interaction.response.send_message(
            f"No games recorded in **{sc.label}**"
            + (f" for **{pname}**." if pname else "."),
            ephemeral=True,
        )
        return

    # Rendering takes longer than Discord's 3s reply window.
    await interaction.response.defer()

    if pname:
        title = f"{pname} — Elo progress ({sc.label}, {len(series[0][1])} matches)"
    else:
        series.sort(key=lambda s: -s[1][-1][1])
        title = f"Elo progress — {sc.label}"
    footnote = f"{sc.total} matches in {sc.label} · ladder built {ladder['scrape_meta']['generated_at'][:10]}"
    if not pname:
        footnote += " · weekly resolution (last rating each week)"

    loop = asyncio.get_running_loop()
    try:
        buf = await loop.run_in_executor(None, render_elo_chart, series, title, footnote)
    except ImportError:
        await interaction.followup.send(
            "Charting needs matplotlib: `pip install -r code/requirements.txt`, then restart the bot."
        )
        return

    fname = f"elo_{(pname or 'all').replace(' ', '_')}_{sc.label.replace(' ', '_')}.png"
    await interaction.followup.send(file=discord.File(buf, filename=fname))


@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"Logged in as {bot.user} — {len(synced)} slash command(s) registered")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running.")
    bot.run(token)

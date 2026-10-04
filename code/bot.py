import os
import re
import sys
import io
import asyncio
import json
import subprocess
from collections import namedtuple
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import fcntl              # the server is Linux; Windows (local testing) runs unlocked
except ImportError:
    fcntl = None

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

    # Charting takes longer than Discord's 3s reply window, so every reply below
    # goes through followup.
    await interaction.response.defer()

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
        await send_stats_with_chart(interaction, embed, ladder, pname, sc)
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
        await send_stats_with_chart(interaction, embed, ladder, pname, sc)
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

    await send_stats_with_chart(interaction, embed, ladder, pname, sc)


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


def _series_for(ladder, names, scope):
    """[(name, [(datetime, elo, won), ...])] for the players that have history."""
    series = []
    for name in names:
        pts = []
        for h in scope_history(ladder, name, scope):
            if not h.get("date"):
                continue
            pts.append((datetime.fromisoformat(h["date"]), h["elo_after"], h["won"]))
        pts.sort(key=lambda t: t[0])
        if pts:
            series.append((name, pts))
    return series


async def _render_async(series, title, footnote):
    """Render off the event loop. None if matplotlib isn't installed."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, render_elo_chart, series, title, footnote)
    except ImportError:
        return None


async def send_stats_with_chart(interaction, embed, ladder, pname, scope):
    """Send a /playerstats embed with its Elo chart attached inside it.

    Falls back to the embed alone when there's nothing to plot (no games in
    scope) or matplotlib is missing, so stats never fail because of charting.
    """
    series = _series_for(ladder, [pname], scope)
    if not series:
        await interaction.followup.send(embed=embed)
        return

    title = f"{pname} — Elo progress ({scope.label}, {len(series[0][1])} matches)"
    footnote = (f"{scope.total} matches in {scope.label} · "
                f"ladder built {ladder['scrape_meta']['generated_at'][:10]}")
    buf = await _render_async(series, title, footnote)
    if buf is None:
        embed.description = ((embed.description or "") +
                             "\n_Chart unavailable: matplotlib isn't installed._").strip()
        await interaction.followup.send(embed=embed)
        return

    fname = f"elo_{pname.replace(' ', '_')}_{scope.label.replace(' ', '_')}.png"
    embed.set_image(url=f"attachment://{fname}")
    await interaction.followup.send(embed=embed, file=discord.File(buf, filename=fname))


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
    series = _series_for(ladder, wanted, sc)

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

    buf = await _render_async(series, title, footnote)
    if buf is None:
        await interaction.followup.send(
            "Charting needs matplotlib: `pip install -r code/requirements.txt`, then restart the bot."
        )
        return

    fname = f"elo_{(pname or 'all').replace(' ', '_')}_{sc.label.replace(' ', '_')}.png"
    await interaction.followup.send(file=discord.File(buf, filename=fname))


# ---------- /addplayer ----------
# The player list is data/players.json, which the whole data pipeline reads via
# tracked_players.py. Adding a player commits and pushes that file from this
# checkout; the Windows PC pulls before every fetch, so the player is tracked
# from the next ladder update. The bot must run as the user whose git identity
# and SSH key can push - the same checkout deploy/pull.sh keeps reset.
REPO_DIR = os.environ.get("REPO_DIR", str(Path(__file__).resolve().parent.parent))
PLAYERS_PATH = os.path.join(DATA_DIR, "players.json")
REPO_LOCK_PATH = os.path.join(REPO_DIR, ".git", "aoe2-repo.lock")   # shared with pull.sh
PROFILE_RE = re.compile(r"^\s*(?:https?://)?(?:www\.)?(?:aoe2insights\.com/user/)?(\d+)/?\s*$", re.I)
# Names become graph filenames (data/graphs/elo_<name>.png), so nothing a
# filesystem rejects, and nothing that breaks Discord formatting.
NAME_RE = re.compile(r'^[^\\/:*?"<>|`]{1,32}$')


class PlayerError(Exception):
    """A reason, fit to show the user, that a player change can't be made."""


def load_player_list():
    with open(PLAYERS_PATH, encoding="utf-8") as f:
        return json.load(f)["players"]


def check_new_player(players, entry):
    """Raise PlayerError if entry clashes with a player already in the list."""
    for p in players:
        if p["id"] == entry["id"]:
            raise PlayerError(f"aoe2insights profile {entry['id']} is already tracked as **{p['name']}**.")
        if p["name"].lower() == entry["name"].lower():
            raise PlayerError(f"There's already a player called **{p['name']}**.")
        if p.get("discord_id") == entry["discord_id"]:
            raise PlayerError(f"<@{entry['discord_id']}> is already linked to **{p['name']}**.")


def _git(*args):
    return subprocess.run(
        ["git", "-C", REPO_DIR, *args], capture_output=True, text=True, timeout=60,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _git_ok(*args):
    r = _git(*args)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()}")


def commit_players_change(change, subject, details, attempts=3):
    """Apply change(players) to players.json, commit and push. Blocking - run in a thread.

    change edits the list in place and returns anything the caller wants back,
    or raises PlayerError. It runs against a freshly fetched origin/main on every
    attempt, so its checks always see the latest list.

    Holds the lock pull.sh takes, so its reset can't land between our commit and
    push. A rejected push (the PC pushed a ladder update in between) just retries
    on top of it. On any failure the checkout is put back to origin/main, so no
    unpushed commit is left.
    """
    rel_path = os.path.relpath(PLAYERS_PATH, REPO_DIR).replace(os.sep, "/")
    with open(REPO_LOCK_PATH, "w") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            for _ in range(attempts):
                _git_ok("fetch", "--quiet", "origin", "main")
                _git_ok("reset", "--hard", "--quiet", "origin/main")
                with open(PLAYERS_PATH, encoding="utf-8") as f:
                    doc = json.load(f)
                result = change(doc["players"])
                with open(PLAYERS_PATH, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(doc, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                _git_ok("add", rel_path)
                _git_ok("commit", "--quiet", "-m", subject, "-m", details)
                if _git("push", "--quiet", "origin", "HEAD:main").returncode == 0:
                    return result
            raise RuntimeError(f"the push was rejected {attempts} times in a row - try again in a minute")
        except Exception:
            _git("reset", "--hard", "--quiet", "origin/main")
            raise


def commit_new_player(entry):
    """Append entry to players.json and push it. Blocking - run in a thread."""
    def add(players):
        check_new_player(players, entry)
        players.append(entry)

    commit_players_change(
        add, f"Add player {entry['name']} via Discord",
        f"Profile: https://www.aoe2insights.com/user/{entry['id']}/\n"
        f"Discord: {entry['discord_name']} ({entry['discord_id']})\n"
        f"Counts from: {entry['since']}\nAdded by: {entry['added_by']}",
    )


def link_change(player, member):
    """A players.json change that links `player` (name) to a Discord member.

    Returns (ladder name, previously linked Discord id or None). Refuses a member
    already linked to another player, and a link that's already in place.
    """
    def link(players):
        p = next((q for q in players if q["name"].lower() == player.strip().lower()), None)
        if p is None:
            raise PlayerError(f"**{player}** isn't a tracked player.")
        did = str(member.id)
        if p.get("discord_id") == did:
            raise PlayerError(f"**{p['name']}** is already linked to <@{did}>.")
        other = next((q for q in players if q.get("discord_id") == did), None)
        if other is not None:
            raise PlayerError(f"<@{did}> is already linked to **{other['name']}**.")
        previous = p.get("discord_id")
        p["discord_id"] = did
        p["discord_name"] = member.display_name
        return p["name"], previous

    return link


class AddPlayerConfirm(discord.ui.View):
    """Ephemeral Add / Cancel step, so a typo'd link never reaches the repo."""

    def __init__(self, requester_id, entry):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.entry = entry

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.requester_id

    @discord.ui.button(label="Add player", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Adding…", view=None)
        try:
            await asyncio.to_thread(commit_new_player, self.entry)
        except PlayerError as e:
            await interaction.edit_original_response(content=f"❌ {e}")
            return
        except Exception as e:
            print(f"/addplayer failed for {self.entry}: {e!r}")
            await interaction.edit_original_response(content=f"❌ Couldn't save the player: {e}")
            return

        e = self.entry
        await interaction.edit_original_response(content="Player added ✅")
        # Public, so the channel knows - but without pinging anyone.
        await interaction.channel.send(
            f"➕ **{e['name']}** (<@{e['discord_id']}>) joins the ladder, counting games from "
            f"{e['since']}. Added by {interaction.user.mention}; they'll show up in `/players` "
            f"after the next ladder update.\n<https://www.aoe2insights.com/user/{e['id']}/>",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled - nothing was added.", view=None)


@bot.tree.command(name="addplayer", description="Add a player to the ladder")
@app_commands.describe(
    discord_user="The player's Discord account",
    name="Name to show on the ladder, usually their in-game name",
    profile="Their aoe2insights profile link, e.g. https://www.aoe2insights.com/user/13194886/",
    since="First day their games count, YYYY-MM-DD (default: today)",
)
@app_commands.guild_only()
async def addplayer_cmd(interaction: discord.Interaction, discord_user: discord.Member,
                        name: str, profile: str, since: str | None = None):
    async def reject(msg):
        await interaction.response.send_message(f"❌ {msg}", ephemeral=True)

    if discord_user.bot:
        return await reject("That's a bot account - pick the player's own Discord account.")
    m = PROFILE_RE.match(profile)
    if not m:
        return await reject("`profile` must be an aoe2insights profile link like "
                            "https://www.aoe2insights.com/user/13194886/ (or just the number).")
    name = " ".join(name.split())
    if not NAME_RE.match(name):
        return await reject('`name` must be 1-32 characters, without \\ / : * ? " < > | or `.')

    today = datetime.now(timezone.utc).date()
    if since:
        try:
            since_d = date.fromisoformat(since.strip())
        except ValueError:
            return await reject("`since` must be a date like 2026-10-03.")
        if since_d > today:
            return await reject("`since` can't be in the future.")
    else:
        since_d = today

    entry = {
        "id": int(m.group(1)),
        "name": name,
        "since": since_d.isoformat(),
        "discord_id": str(discord_user.id),
        "discord_name": discord_user.display_name,
        "added_by": interaction.user.name,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        check_new_player(load_player_list(), entry)
    except PlayerError as e:
        return await reject(str(e))

    link = f"https://www.aoe2insights.com/user/{entry['id']}/"
    await interaction.response.send_message(
        f"Add this player to the ladder?\n"
        f"**Name:** {name}\n**Discord:** {discord_user.mention}\n"
        f"**aoe2insights:** <{link}> - open it and check it's the right account\n"
        f"**Games count from:** {entry['since']}",
        view=AddPlayerConfirm(interaction.user.id, entry),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# ---------- /linkplayer ----------
# Stores the member's Discord user id (permanent - survives renames and
# nickname changes) on the player's players.json entry, via the same commit and
# push as /addplayer. The ladder builders ignore it, so nothing is rebuilt; the
# bot's own checkout has the link as soon as the push succeeds.

# Post each new link to the channel too. Off while the existing players are
# being linked in one go, so the channel isn't spammed; the person linking
# always gets a private confirmation either way.
ANNOUNCE_LINKS = False

async def listed_player_autocomplete(interaction: discord.Interaction, current: str):
    """Every player in players.json - including ones added since the last ladder
    build - with who they're linked to, so it's easy to see who's left."""
    current = current.lower()
    choices = []
    for p in load_player_list():
        if current not in p["name"].lower():
            continue
        label = p["name"] + (f" — linked to {p['discord_name']}" if p.get("discord_id") else " — not linked")
        choices.append(app_commands.Choice(name=label[:100], value=p["name"]))
    return choices[:25]


@bot.tree.command(name="linkplayer", description="Link a ladder player to their Discord account")
@app_commands.describe(player="Ladder player", discord_user="Their Discord account")
@app_commands.autocomplete(player=listed_player_autocomplete)
@app_commands.guild_only()
async def linkplayer_cmd(interaction: discord.Interaction, player: str, discord_user: discord.Member):
    if discord_user.bot:
        return await interaction.response.send_message(
            "❌ That's a bot account - pick the player's own Discord account.", ephemeral=True)

    change = link_change(player, discord_user)
    try:
        name, _ = change(load_player_list())    # fail fast on the local copy before touching git
    except PlayerError as e:
        return await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    try:
        name, previous = await asyncio.to_thread(
            commit_players_change, change, f"Link player {name} to Discord user {discord_user.display_name}",
            f"Discord: {discord_user.display_name} ({discord_user.id})\nLinked by: {interaction.user.name}",
        )
    except PlayerError as e:
        return await interaction.followup.send(f"❌ {e}", ephemeral=True)
    except Exception as e:
        print(f"/linkplayer failed for {player} -> {discord_user.id}: {e!r}")
        return await interaction.followup.send(f"❌ Couldn't save the link: {e}", ephemeral=True)

    note = f" (was <@{previous}>)" if previous else ""
    await interaction.followup.send(
        f"✅ **{name}** is now linked to {discord_user.mention}{note}.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )
    if ANNOUNCE_LINKS:
        await interaction.channel.send(
            f"🔗 **{name}** is now linked to {discord_user.mention}{note}. "
            f"Linked by {interaction.user.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"Logged in as {bot.user} — {len(synced)} slash command(s) registered")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running.")
    bot.run(token)

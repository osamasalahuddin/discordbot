import os
import json
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

# A player needs at least this many games on a map before that map's Elo is
# trusted. Below the threshold the bot falls back to the player's overall Elo.
FALLBACK_MIN_GAMES = 5


def load_ladder():
    with open(LADDER_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_players():
    return {name: p["current_elo"] for name, p in load_ladder()["players"].items()}


def load_map_elo():
    try:
        with open(MAP_ELO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def map_names_by_popularity():
    """All maps with Elo data, most-played first. 'Unknown' is dropped."""
    data = load_map_elo()
    maps = [(name, v.get("total_matches", 0)) for name, v in data.items() if name != "Unknown"]
    maps.sort(key=lambda kv: -kv[1])
    return [name for name, _ in maps]


def effective_elo(name, overall_elo, map_name, map_elo_data):
    """Return (elo, used_map_elo) for a player on the chosen map.

    Uses the per-map Elo only when the player has >= FALLBACK_MIN_GAMES games on
    that map; otherwise falls back to their overall ladder Elo.
    """
    if not map_name:
        return overall_elo, False
    entry = map_elo_data.get(map_name, {}).get("players", {}).get(name)
    if entry and entry.get("matches_played", 0) >= FALLBACK_MIN_GAMES:
        return entry["current_elo"], True
    return overall_elo, False


def build_embed(result, map_name=None, fallback_names=None):
    fallback_names = set(fallback_names or [])

    def fmt(team):
        return "\n".join(
            f"- {name} ({int(round(elo))}){' *' if name in fallback_names else ''}"
            for name, elo in team
        )

    team_a_label = "Team A" + (" 🤖" if result["ai_team"] == "A" else "")
    team_b_label = "Team B" + (" 🤖" if result["ai_team"] == "B" else "")

    title = "Balanced Teams" + (f" — {map_name}" if map_name else "")
    embed = discord.Embed(title=title, color=0x3B6EA5)
    embed.add_field(name=f"{team_a_label} — avg {result['avg_a']:.0f}", value=fmt(result["team_a"]), inline=True)
    embed.add_field(name=f"{team_b_label} — avg {result['avg_b']:.0f}", value=fmt(result["team_b"]), inline=True)

    footer = f"Elo gap: {result['elo_diff']:.0f}"
    if result["ai_added"]:
        footer += " | AI added to even out team size"
    if map_name:
        footer += f" | using {map_name} Elo"
        if fallback_names:
            footer += f" | * overall Elo (<{FALLBACK_MIN_GAMES} games on {map_name})"
    embed.set_footer(text=footer)
    return embed


class PlayerSelect(discord.ui.Select):
    def __init__(self, players, map_name, map_elo_data):
        self.players = players
        self.map_name = map_name
        self.map_elo_data = map_elo_data
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
        chosen = []
        fallback_names = []
        for name in self.values:
            elo, used_map = effective_elo(name, self.players[name], self.map_name, self.map_elo_data)
            chosen.append((name, elo))
            if self.map_name and not used_map:
                fallback_names.append(name)

        result = balance_teams(chosen)
        embed = build_embed(result, self.map_name, fallback_names)
        # The selection prompt is ephemeral (only the caller picks players); post
        # the balanced teams as a new public message so the whole channel sees them.
        await interaction.response.edit_message(content="Teams generated ✅", view=None)
        await interaction.channel.send(embed=embed)


class PlayerSelectView(discord.ui.View):
    def __init__(self, players, map_name, map_elo_data):
        super().__init__(timeout=120)
        self.add_item(PlayerSelect(players, map_name, map_elo_data))


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


async def map_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    names = map_names_by_popularity()
    matches = [n for n in names if current in n.lower()][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


async def player_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    players = load_players()
    ordered = sorted(players.items(), key=lambda kv: -kv[1])
    matches = [name for name, _ in ordered if current in name.lower()][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


@bot.tree.command(name="balance", description="Pick players and get balanced teams")
@app_commands.describe(
    map="Optional: balance using per-map Elo (falls back to overall Elo for players with few games on it)"
)
@app_commands.autocomplete(map=map_autocomplete)
async def balance_cmd(interaction: discord.Interaction, map: str | None = None):
    players = load_players()

    map_name = None
    if map:
        map_elo_data = load_map_elo()
        # Accept the value even if the casing differs from the stored key.
        match = next((k for k in map_elo_data if k.lower() == map.lower()), None)
        if match is None:
            await interaction.response.send_message(
                f"No Elo data for a map called **{map}**. Start typing and pick one from the list.",
                ephemeral=True,
            )
            return
        map_name = match
    else:
        map_elo_data = {}

    view = PlayerSelectView(players, map_name, map_elo_data)
    prompt = "Select the players for this match:"
    if map_name:
        prompt = f"Select the players for this **{map_name}** match:"
    await interaction.response.send_message(prompt, view=view, ephemeral=True)


@bot.tree.command(name="players", description="List available tracked players and their Elo")
@app_commands.describe(map="Optional: show per-map Elo for this map instead of overall")
@app_commands.autocomplete(map=map_autocomplete)
async def players_cmd(interaction: discord.Interaction, map: str | None = None):
    players = load_players()

    if map:
        map_elo_data = load_map_elo()
        match = next((k for k in map_elo_data if k.lower() == map.lower()), None)
        if match is None:
            await interaction.response.send_message(
                f"No Elo data for a map called **{map}**.", ephemeral=True
            )
            return
        rows = []
        for name, overall in players.items():
            elo, used_map = effective_elo(name, overall, match, map_elo_data)
            entry = map_elo_data.get(match, {}).get("players", {}).get(name, {})
            games = entry.get("matches_played", 0)
            note = "" if used_map else " *"
            rows.append((name, elo, games, note))
        rows.sort(key=lambda r: -r[1])
        lines = [f"**{name}** — {int(round(elo))}  ({games} games){note}" for name, elo, games, note in rows]
        desc = "\n".join(lines)
        total = map_elo_data.get(match, {}).get("total_matches", 0)
        embed = discord.Embed(
            title=f"Tracked Players — {match}",
            description=desc,
            color=0x3B6EA5,
        )
        embed.set_footer(text=f"{total} matches on {match} | * overall Elo (<{FALLBACK_MIN_GAMES} games on this map)")
        await interaction.response.send_message(embed=embed)
        return

    lines = [f"**{name}** — {int(round(elo))}" for name, elo in sorted(players.items(), key=lambda kv: -kv[1])]
    embed = discord.Embed(title="Tracked Players", description="\n".join(lines), color=0x3B6EA5)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="playerstats", description="Show a player's stats on a specific map")
@app_commands.describe(player="Tracked player", map="Map to look up")
@app_commands.autocomplete(player=player_autocomplete, map=map_autocomplete)
async def playerstats_cmd(interaction: discord.Interaction, player: str, map: str):
    ladder = load_ladder()
    players = ladder["players"]
    map_elo_data = load_map_elo()

    pname = next((k for k in players if k.lower() == player.lower()), None)
    if pname is None:
        await interaction.response.send_message(
            f"**{player}** isn't a tracked player. Try `/players`.", ephemeral=True
        )
        return

    mname = next((k for k in map_elo_data if k.lower() == map.lower()), None)
    if mname is None:
        await interaction.response.send_message(
            f"No Elo data for a map called **{map}**.", ephemeral=True
        )
        return

    overall_elo = players[pname]["current_elo"]
    user_path = players[pname].get("user_path")
    map_entry = map_elo_data.get(mname, {}).get("players", {}).get(pname, {})
    games = map_entry.get("matches_played", 0)

    embed = discord.Embed(title=f"{pname} on {mname}", color=0x3B6EA5)

    if not games:
        embed.description = (
            f"No recorded games on **{mname}**.\n"
            f"Used for balancing: **{int(round(overall_elo))}** (overall Elo)"
        )
        total = map_elo_data.get(mname, {}).get("total_matches", 0)
        embed.set_footer(text=f"{total} tracked matches on {mname}")
        await interaction.response.send_message(embed=embed)
        return

    wins = map_entry.get("wins", 0)
    losses = map_entry.get("losses", 0)
    win_rate = map_entry.get("win_rate", 0.0)
    map_elo = map_entry.get("current_elo", overall_elo)

    # Rank among tracked players who have actually played this map.
    ranked = sorted(
        (
            (n, e["current_elo"])
            for n, e in map_elo_data.get(mname, {}).get("players", {}).items()
            if e.get("matches_played", 0) > 0
        ),
        key=lambda kv: -kv[1],
    )
    rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == pname), None)

    # Recent form + civ usage, from this player's per-match history on the map.
    hist = [h for h in players[pname].get("history", []) if h.get("map") == mname]
    last5 = hist[-5:][::-1]
    form = " ".join("✅" if h["won"] else "❌" for h in last5) or "—"
    net_swing = sum(h.get("delta", 0.0) for h in hist)

    civ_counts = {}
    if user_path:
        for m in ladder.get("match_log", []):
            if m.get("map") != mname:
                continue
            for team in m["teams"]:
                for p in team["players"]:
                    if p.get("user_path") == user_path:
                        civ_counts[p["civ"]] = civ_counts.get(p["civ"], 0) + 1
    top_civs = sorted(civ_counts.items(), key=lambda kv: -kv[1])[:3]
    civ_str = ", ".join(f"{c.title()} ({n})" for c, n in top_civs) or "—"

    rank_str = f"  (#{rank} of {len(ranked)})" if rank else ""
    # /balance and /players don't trust a thin sample: below FALLBACK_MIN_GAMES
    # they use the player's overall Elo instead. Show both here so this view and
    # the balancer never appear to disagree about the same player on the same map.
    balance_elo, uses_map = effective_elo(pname, overall_elo, mname, map_elo_data)
    if uses_map:
        elo_value = f"**{int(round(map_elo))}**{rank_str}\nOverall: {int(round(overall_elo))}"
    else:
        elo_value = (
            f"**{int(round(map_elo))}**{rank_str}\n"
            f"Overall: {int(round(overall_elo))}\n"
            f"Used for balancing: **{int(round(balance_elo))}** (overall)"
        )
    embed.add_field(name="Map Elo", value=elo_value, inline=True)
    embed.add_field(
        name="Record",
        value=f"**{wins}–{losses}**  ({win_rate:.0f}% win rate)\n{games} games",
        inline=True,
    )
    embed.add_field(name="Recent form", value=f"{form}\nNet Elo on map: {net_swing:+.0f}", inline=False)
    embed.add_field(name="Most-played civs", value=civ_str, inline=False)

    first_date = (hist[0].get("date") or "")[:10]
    last_date = (hist[-1].get("date") or "")[:10]
    footer = f"First played {first_date} · last played {last_date}"
    if not uses_map:
        footer += f" | under {FALLBACK_MIN_GAMES} games here, so /balance uses overall Elo"
    embed.set_footer(text=footer)

    await interaction.response.send_message(embed=embed)


@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"Logged in as {bot.user} — {len(synced)} slash command(s) registered")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running.")
    bot.run(token)

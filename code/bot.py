import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands

import ladder_data as data
from ladder_data import FALLBACK_MIN_GAMES
from team_balancer import balance_teams

EMBED_COLOR = 0x3B6EA5
# Discord hard-caps a select menu at 25 options.
MAX_SELECT_OPTIONS = 25


def build_embed(result, map_name=None, fallback_names=None):
    fallback_names = set(fallback_names or [])

    def fmt(team):
        return "\n".join(
            f"- {name} ({int(round(elo))}){' *' if name in fallback_names else ''}"
            for name, elo in sorted(team, key=lambda kv: -kv[1])
        )

    team_a_label = "Team A" + (" 🤖" if result["ai_team"] == "A" else "")
    team_b_label = "Team B" + (" 🤖" if result["ai_team"] == "B" else "")

    title = "Balanced Teams" + (f" — {map_name}" if map_name else "")
    embed = discord.Embed(title=title, color=EMBED_COLOR)
    embed.add_field(name=f"{team_a_label} — avg {result['avg_a']:.0f}", value=fmt(result["team_a"]), inline=True)
    embed.add_field(name=f"{team_b_label} — avg {result['avg_b']:.0f}", value=fmt(result["team_b"]), inline=True)

    footer = f"Elo gap: {result['elo_diff']:.0f} total · {abs(result['avg_a'] - result['avg_b']):.0f} per player"
    if result["ai_added"]:
        ai_elo = next(elo for name, elo in result["team_a"] + result["team_b"] if name == "AI")
        footer += f" | AI ({ai_elo:.0f}) added to even out team size"
    if map_name:
        footer += f" | {map_name} Elo, rescaled onto the overall ladder"
        if fallback_names:
            footer += f" | * overall Elo (<{FALLBACK_MIN_GAMES} games on {map_name})"
    embed.set_footer(text=footer)
    return embed


class PlayerSelect(discord.ui.Select):
    def __init__(self, rated, map_name):
        # rated: name -> (elo, used_map_elo), already on one comparable scale.
        self.rated = rated
        self.map_name = map_name
        ranked = sorted(rated.items(), key=lambda kv: -kv[1][0])[:MAX_SELECT_OPTIONS]
        options = [
            discord.SelectOption(label=f"{name} (Elo {int(round(elo))})", value=name)
            for name, (elo, _) in ranked
        ]
        super().__init__(
            placeholder="Choose players for this match...",
            min_values=2,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = [(name, self.rated[name][0]) for name in self.values]
        fallback_names = data.fallback_names(self.rated, self.values, self.map_name)

        # Exhaustive for realistic lobbies, but keep it off the event loop so a large
        # selection can never stall the gateway heartbeat.
        result = await asyncio.to_thread(balance_teams, chosen)
        embed = build_embed(result, self.map_name, fallback_names)

        self.view.stop()

        # The selection prompt is ephemeral (only the caller picks players); post the
        # balanced teams as a new public message so the whole channel sees them. If that
        # isn't possible, show the result to the caller rather than dropping it and
        # claiming success.
        channel = interaction.channel
        if channel is not None:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass
            else:
                await interaction.response.edit_message(content="Teams generated ✅", view=None)
                return
        await interaction.response.edit_message(
            content="Couldn't post to the channel — here are your teams:",
            embed=embed,
            view=None,
        )


class PlayerSelectView(discord.ui.View):
    def __init__(self, rated, map_name):
        super().__init__(timeout=120)
        self.interaction = None
        self.add_item(PlayerSelect(rated, map_name))

    async def on_timeout(self):
        # Ephemeral messages can only be edited through the interaction that sent them.
        if self.interaction is None:
            return
        try:
            await self.interaction.edit_original_response(
                content="Selection timed out — run `/balance` again.", view=None
            )
        except discord.HTTPException:
            pass


class LadderBot(commands.Bot):
    async def setup_hook(self):
        # Once per process. on_ready fires again on every gateway resume, so syncing
        # there re-registered the whole command tree on each reconnect.
        synced = await self.tree.sync()
        print(f"{len(synced)} slash command(s) registered")


intents = discord.Intents.default()
bot = LadderBot(command_prefix="!", intents=intents)


async def map_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    names = data.map_names_by_popularity()
    matches = [n for n in names if current in n.lower()][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


async def player_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    players = data.load_players()
    ordered = sorted(players.items(), key=lambda kv: -kv[1])
    matches = [name for name, _ in ordered if current in name.lower()][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


async def _reject_unknown_map(interaction, name, hint=""):
    await interaction.response.send_message(
        f"No Elo data for a map called **{name}**.{hint}", ephemeral=True
    )


@bot.tree.command(name="balance", description="Pick players and get balanced teams")
@app_commands.describe(
    map="Optional: balance using per-map Elo (falls back to overall Elo for players with few games on it)"
)
@app_commands.autocomplete(map=map_autocomplete)
async def balance_cmd(interaction: discord.Interaction, map: str | None = None):
    players = data.load_players()

    map_elo_data = data.load_map_elo() if map else {}
    map_name = None
    if map:
        map_name = data.resolve_map_name(map_elo_data, map)
        if map_name is None:
            await _reject_unknown_map(interaction, map, " Start typing and pick one from the list.")
            return

    rated = data.map_adjusted_elos(players, map_name, map_elo_data)

    view = PlayerSelectView(rated, map_name)
    prompt = "Select the players for this match:"
    if map_name:
        prompt = f"Select the players for this **{map_name}** match:"
    if len(rated) > MAX_SELECT_OPTIONS:
        prompt += f"\n-# Showing the top {MAX_SELECT_OPTIONS} by Elo (Discord's select limit)."
    await interaction.response.send_message(prompt, view=view, ephemeral=True)
    view.interaction = interaction


@bot.tree.command(name="players", description="List available tracked players and their Elo")
@app_commands.describe(map="Optional: show per-map Elo for this map instead of overall")
@app_commands.autocomplete(map=map_autocomplete)
async def players_cmd(interaction: discord.Interaction, map: str | None = None):
    players = data.load_players()

    if map:
        map_elo_data = data.load_map_elo()
        map_name = data.resolve_map_name(map_elo_data, map)
        if map_name is None:
            await _reject_unknown_map(interaction, map)
            return

        # Same numbers the balancer uses, so the two commands can't disagree.
        rated = data.map_adjusted_elos(players, map_name, map_elo_data)
        rows = sorted(
            (
                (name, elo, data.map_games_played(map_elo_data, map_name, name), "" if used_map else " *")
                for name, (elo, used_map) in rated.items()
            ),
            key=lambda row: -row[1],
        )
        desc = "\n".join(
            f"**{name}** — {int(round(elo))}  ({games} games){note}" for name, elo, games, note in rows
        )
        total = map_elo_data.get(map_name, {}).get("total_matches", 0)
        embed = discord.Embed(title=f"Tracked Players — {map_name}", description=desc, color=EMBED_COLOR)
        embed.set_footer(
            text=f"{total} matches on {map_name} | rescaled onto the overall ladder"
                 f" | * overall Elo (<{FALLBACK_MIN_GAMES} games on this map)"
        )
        await interaction.response.send_message(embed=embed)
        return

    lines = [f"**{name}** — {int(round(elo))}" for name, elo in sorted(players.items(), key=lambda kv: -kv[1])]
    embed = discord.Embed(title="Tracked Players", description="\n".join(lines), color=EMBED_COLOR)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="playerstats", description="Show a player's stats on a specific map")
@app_commands.describe(player="Tracked player", map="Map to look up")
@app_commands.autocomplete(player=player_autocomplete, map=map_autocomplete)
async def playerstats_cmd(interaction: discord.Interaction, player: str, map: str):
    ladder = data.load_ladder()
    players = ladder["players"]
    map_elo_data = data.load_map_elo()

    pname = data.resolve_player_name(players, player)
    if pname is None:
        await interaction.response.send_message(
            f"**{player}** isn't a tracked player. Try `/players`.", ephemeral=True
        )
        return

    mname = data.resolve_map_name(map_elo_data, map)
    if mname is None:
        await _reject_unknown_map(interaction, map)
        return

    overall_elo = players[pname]["current_elo"]
    user_path = players[pname].get("user_path")
    map_entry = map_elo_data.get(mname, {}).get("players", {}).get(pname, {})
    games = map_entry.get("matches_played", 0)

    embed = discord.Embed(title=f"{pname} on {mname}", color=EMBED_COLOR)

    if not games:
        embed.description = f"No recorded games on **{mname}**.\nOverall Elo: **{int(round(overall_elo))}**"
        total = map_elo_data.get(mname, {}).get("total_matches", 0)
        embed.set_footer(text=f"{total} tracked matches on {mname}")
        await interaction.response.send_message(embed=embed)
        return

    wins = map_entry.get("wins", 0)
    losses = map_entry.get("losses", 0)
    win_rate = map_entry.get("win_rate", 0.0)
    map_elo = map_entry.get("current_elo", overall_elo)

    # Rank among tracked players who have actually played this map.
    ranked = data.map_standings(map_elo_data, mname)
    rank = next((i + 1 for i, (name, _) in enumerate(ranked) if name == pname), None)

    # Recent form + civ usage, from this player's per-match history on the map.
    hist = data.player_map_history(ladder, pname, mname)
    last5 = hist[-5:][::-1]
    form = " ".join("✅" if h["won"] else "❌" for h in last5) or "—"
    net_swing = sum(h.get("delta", 0.0) for h in hist)
    civ_str = ", ".join(f"{civ} ({n})" for civ, n in data.top_civs(ladder, user_path, mname)) or "—"

    rank_str = f"  (#{rank} of {len(ranked)})" if rank else ""
    embed.add_field(
        name="Map Elo",
        value=f"**{int(round(map_elo))}**{rank_str}\nOverall: {int(round(overall_elo))}",
        inline=True,
    )
    embed.add_field(
        name="Record",
        value=f"**{wins}–{losses}**  ({win_rate:.0f}% win rate)\n{games} games",
        inline=True,
    )
    embed.add_field(name="Recent form", value=f"{form}\nNet Elo on map: {net_swing:+.0f}", inline=False)
    embed.add_field(name="Most-played civs", value=civ_str, inline=False)

    first_date = (hist[0].get("date") or "")[:10] if hist else ""
    last_date = (hist[-1].get("date") or "")[:10] if hist else ""
    embed.set_footer(
        text=f"First played {first_date} · last played {last_date}"
             " · each map has its own Elo scale, starting from 1000"
    )

    await interaction.response.send_message(embed=embed)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running.")
    bot.run(token)

// Self-contained incremental update pipeline for aoe2insights.com tracked players.
// Paste into the browser JS console (or javascript_tool) on any aoe2insights.com page.
//
// Usage:
//   1. Get the current known match IDs: run `python get_known_match_ids.py`, it writes
//      E:\Work\Claude\data\perf_chunks\known_ids.json
//   2. Paste this whole file into javascript_tool to define everything.
//   3. Run: await window.__runIncrementalUpdate(<contents of known_ids.json>)
//      This does all 4 phases: update stale profiles, discover new matches, filter to
//      qualifying ones (2+ tracked players on opposing teams), analyze + fetch performance
//      data for each qualifying new match.
//   4. Export with window.__exportIncremental() (triggers a download), move the file into
//      E:\Work\Claude\data\, then run merge_incremental_update.py pointed at it.
//
// Same throttling caveats as harvest_performance.js apply: if timeouts pile up, stop and
// resume later rather than pushing through.

window.__TRACKED_USERS = {
  12047120: "wabbit", 12676944: "SauronSlayer", 12667372: "zubair",
  12080589: "l.inc", 12499000: "toXic", 4607974: "Strength & Honour",
  11907023: "cheetah001", 12693189: "NaKiyaKar", 12805097: "neXus",
};
window.__TRACKED_PATHS = new Set(Object.keys(window.__TRACKED_USERS).map((id) => `/user/${id}/`));

window.__getCookie = function (name) {
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return match ? match[2] : null;
};

// ---- Phase 1: update stale profiles ----
window.__updateProfiles = async function () {
  const results = {};
  for (const id of Object.keys(window.__TRACKED_USERS)) {
    const res = await fetch(`/user/${id}/`, { credentials: "same-origin" });
    const text = await res.text();
    const outdated = /outdated/i.test(text);
    if (outdated) {
      const csrftoken = window.__getCookie("csrftoken");
      const updRes = await fetch(`/user/${id}/update-match-history`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrftoken },
      });
      results[id] = { name: window.__TRACKED_USERS[id], was_outdated: true, update_status: updRes.status };
    } else {
      results[id] = { name: window.__TRACKED_USERS[id], was_outdated: false };
    }
    await new Promise((r) => setTimeout(r, 800));
  }
  return results;
};

// ---- List-page parser (same shape as harvest_performance list scraping) ----
window.__parseListDoc = function (doc) {
  const tiles = doc.querySelectorAll("div.match-tile.card");
  return Array.from(tiles).map((tile) => {
    const idSpan = tile.querySelector(".match-id");
    const match_id = idSpan ? parseInt(idSpan.textContent.replace("#", "").trim()) : null;
    const map = tile.querySelector(".match-map") ? tile.querySelector(".match-map").textContent.trim() : null;
    const metaDivs = tile.querySelectorAll(".match-meta > div");
    const duration = metaDivs[0] ? metaDivs[0].textContent.trim() : null;
    const agoSpan = tile.querySelector(".match-meta span[title]");
    const exact_time = agoSpan ? agoSpan.getAttribute("title") : null;
    const teamDivs = tile.querySelectorAll(".teams > .team");
    const teams = Array.from(teamDivs).map((teamEl) => {
      const won = teamEl.classList.contains("won");
      const players = Array.from(teamEl.querySelectorAll(".team-player")).map((p) => {
        const a = p.querySelector('a[href*="/user/"]');
        const civImg = p.querySelector("img:not(.player-avatar)");
        const txt = p.textContent.replace(/\s+/g, " ").trim();
        const m = txt.match(/^(.*?)\s([\d,]+)\s([+\-]\d+)$/);
        return {
          user_path: a ? new URL(a.href).pathname : null,
          name: m ? m[1].trim() : txt,
          rating: m ? parseInt(m[2].replace(/,/g, "")) : null,
          rating_change: m ? parseInt(m[3]) : null,
          civ: civImg ? civImg.src.split("/").pop().replace(".png", "") : null,
          is_ai: !a,
        };
      });
      return { won, players };
    });
    return { match_id, map, duration, exact_time, teams, ladder: "unranked" };
  });
};

// ---- Phase 2: discover new matches per player until a known match_id is hit ----
window.__discoverNewMatches = async function (userId, knownIdsSet, maxPages = 10) {
  const found = {};
  for (let page = 1; page <= maxPages; page++) {
    const res = await fetch(`/user/${userId}/matches/?ladder=0&page=${page}`, { credentials: "same-origin" });
    if (!res.ok) break;
    const html = await res.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const rows = window.__parseListDoc(doc);
    if (rows.length === 0) break;
    let hitKnown = false;
    for (const r of rows) {
      if (!r.match_id) continue;
      if (knownIdsSet.has(r.match_id)) {
        hitKnown = true;
        break;
      }
      found[r.match_id] = r;
    }
    if (hitKnown) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  return found;
};

window.__discoverAllNew = async function (knownIdsArray) {
  const knownIdsSet = new Set(knownIdsArray);
  const allNew = {};
  const perPlayerCounts = {};
  for (const id of Object.keys(window.__TRACKED_USERS)) {
    const found = await window.__discoverNewMatches(id, knownIdsSet);
    perPlayerCounts[window.__TRACKED_USERS[id]] = Object.keys(found).length;
    Object.assign(allNew, found);
    await new Promise((r) => setTimeout(r, 500));
  }
  return { allNew, perPlayerCounts };
};

// ---- Phase 3: filter to matches with 2+ tracked players on opposing teams ----
window.__filterQualifying = function (newMatchesObj) {
  const qualifying = {};
  for (const [id, m] of Object.entries(newMatchesObj)) {
    const teamTracked = m.teams.map(
      (team) => new Set(team.players.filter((p) => window.__TRACKED_PATHS.has(p.user_path)).map((p) => p.user_path))
    );
    const teamsWithTracked = teamTracked.filter((s) => s.size > 0);
    if (teamsWithTracked.length >= 2) {
      qualifying[id] = m;
    }
  }
  return qualifying;
};

// ---- Phase 4: analyze + fetch performance data (same mechanics as harvest_performance.js) ----
window.__perfData = window.__perfData || {};
window.__perfStatus = window.__perfStatus || {};
window.__rateLimited = false;

window.__extractPerf = function (analysisJson) {
  const players = analysisJson.player;
  const apmAgg = analysisJson.advanced_apm.player_apm_aggregations;
  const uptimes = analysisJson.uptimes || {};
  const strategy = analysisJson.strategy || {};
  const out = {};
  for (const pid of Object.keys(players)) {
    const p = players[pid];
    out[pid] = {
      profile_id: p.profile_id,
      name: p.name,
      team: p.team,
      civ: p.civilization ? p.civilization.label : null,
      is_ai: p.type !== "human",
      eapm_mean: apmAgg.total_mean ? apmAgg.total_mean[pid] : null,
      eapm_max: apmAgg.total_max ? apmAgg.total_max[pid] : null,
      eco_apm_mean: apmAgg.mean && apmAgg.mean[pid] ? apmAgg.mean[pid].eco : null,
      military_apm_mean: apmAgg.mean && apmAgg.mean[pid] ? apmAgg.mean[pid].military : null,
      feudal_time_ms: uptimes[pid] ? uptimes[pid].feudal : null,
      castle_time_ms: uptimes[pid] ? uptimes[pid].castle : null,
      imperial_time_ms: uptimes[pid] ? uptimes[pid].imperial : null,
      opening: strategy[pid] || null,
    };
  }
  return out;
};

window.__processMatch = async function (matchId) {
  if (window.__perfData[matchId] || window.__perfStatus[matchId]) return "skip";
  let attempts = 0;
  while (attempts < 2) {
    attempts++;
    let res;
    try {
      res = await fetch(`/match/${matchId}/analyze/`, { credentials: "same-origin" });
    } catch (e) {
      window.__perfStatus[matchId] = "fetch_error";
      return "error";
    }
    if (res.status === 403) {
      window.__rateLimited = true;
      return "rate_limited";
    }
    if (res.status === 404) {
      window.__perfStatus[matchId] = "unavailable";
      return "unavailable";
    }
    if (res.status === 200) {
      let info;
      try {
        info = await res.json();
      } catch (e) {
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      let dres;
      try {
        dres = await fetch(info.analysis, { credentials: "same-origin" });
      } catch (e) {
        window.__perfStatus[matchId] = "fetch_error";
        return "error";
      }
      if (dres.status === 403) {
        window.__rateLimited = true;
        return "rate_limited";
      }
      if (dres.status === 404) {
        window.__perfStatus[matchId] = "data_missing";
        return "data_missing";
      }
      try {
        const data = await dres.json();
        window.__perfData[matchId] = window.__extractPerf(data);
        window.__perfStatus[matchId] = "ok";
        return "ok";
      } catch (e) {
        window.__perfStatus[matchId] = "parse_error";
        return "error";
      }
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  window.__perfStatus[matchId] = "timeout";
  return "timeout";
};

window.__processBatch = async function (matchIds) {
  for (const id of matchIds) {
    if (window.__rateLimited) break;
    try {
      await window.__processMatch(id);
    } catch (e) {
      window.__perfStatus[id] = "unhandled_error";
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  const statuses = Object.values(window.__perfStatus);
  const counts = {};
  for (const s of statuses) counts[s] = (counts[s] || 0) + 1;
  return { rateLimited: window.__rateLimited, totalDone: statuses.length, counts };
};

// ---- Full orchestration ----
window.__runIncrementalUpdate = async function (knownIdsArray) {
  const profileResults = await window.__updateProfiles();

  const { allNew, perPlayerCounts } = await window.__discoverAllNew(knownIdsArray);
  window.__newRawMatches = allNew;

  const qualifying = window.__filterQualifying(allNew);
  const qualifyingIds = Object.keys(qualifying).map(Number);
  window.__qualifyingNewMatchIds = qualifyingIds;

  const perfSummary = await window.__processBatch(qualifyingIds);

  return {
    profileResults,
    newMatchesFound: Object.keys(allNew).length,
    perPlayerNewMatchCounts: perPlayerCounts,
    qualifyingNewMatches: qualifyingIds.length,
    perfSummary,
  };
};

window.__exportIncremental = function () {
  const payload = {
    newRawMatches: window.__newRawMatches || {},
    perfData: window.__perfData || {},
    perfStatus: window.__perfStatus || {},
  };
  const json = JSON.stringify(payload);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "incremental_update_export.json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return "triggered size=" + json.length;
};

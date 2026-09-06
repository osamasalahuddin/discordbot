// Paste this into the browser JS console (or javascript_tool) on any aoe2insights.com page
// to set up the match-performance harvester. Then call window.__processBatch([id1, id2, ...])
// with small batches (10-20 match IDs at a time, fewer if timeouts start piling up).
//
// After harvesting, export with:
//   (() => {
//     const payload = {perfData: window.__perfData, perfStatus: window.__perfStatus};
//     const json = JSON.stringify(payload);
//     const blob = new Blob([json], {type: 'application/json'});
//     const url = URL.createObjectURL(blob);
//     const a = document.createElement('a');
//     a.href = url;
//     a.download = 'match_performance_partial.json';
//     document.body.appendChild(a);
//     a.click();
//     document.body.removeChild(a);
//     setTimeout(() => URL.revokeObjectURL(url), 5000);
//   })()
// Then move the downloaded file into data/ and run merge_performance_data.py (point it at
// the new file) to fold results into the persistent match_performance.json.
//
// IMPORTANT: this site throttles repeated /analyze/ calls within a session. If timeouts start
// dominating results, stop and resume in a fresh session/day rather than pushing through.

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
      // The analyze/ endpoint can report success while the underlying data file has
      // been evicted from storage (seen for matches ~months old) - this is permanent,
      // not a timing race, so treat it as terminal rather than retrying.
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

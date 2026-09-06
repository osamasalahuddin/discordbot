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
// Then move the downloaded file into this data/ folder and run merge_performance_data.py
// (point it at the new file) to fold results into the persistent match_performance.json.
//
// IMPORTANT: this site throttles repeated /analyze/ calls within a session. If timeouts start
// dominating results, stop and resume in a fresh session/day rather than pushing through.

window.__perfData = window.__perfData || {};
window.__perfStatus = window.__perfStatus || {};
window.__rateLimited = false;

// ============================================================================
//  __extractPerf  --  KEEP THIS BLOCK IDENTICAL TO incremental_update.js
// ----------------------------------------------------------------------------
//  schema 2 (2026-09): full extraction from the aoe2insights analysis JSON.
//  Per-match dict shape: { "_meta": {...}, "<pid>": {...}, ... }
//  Time-unit gotchas in the source:
//    units.queued_units[].time  -> SECONDS (float)
//    techs.queued_techs[].time  -> MILLISECONDS
//    replay.actions keys        -> MILLISECONDS
//    uptimes / duration         -> MILLISECONDS  (can be {} for a player)
// ============================================================================
window.__PERF_SCHEMA = 2;
window.__PERF_CHECKPOINTS_S = [300, 600, 900, 1200, 1500, 1800, 2400, 3000, 3600];
// unit_type ids that are NOT military (excluded from military counts/composition)
window.__NON_MILITARY_UNIT_TYPES = { 83: "Villager", 13: "Fishing Ship", 128: "Trade Cart", 354: "Trade Cog" };

window.__dictToMinuteArray = function (obj, dp) {
  if (!obj || typeof obj !== "object") return null;
  const keys = Object.keys(obj).map(Number).filter((n) => !Number.isNaN(n));
  if (!keys.length) return null;
  const n = Math.max.apply(null, keys) + 1;
  const f = dp != null ? Math.pow(10, dp) : null;
  const arr = new Array(n).fill(null);
  for (const k of keys) {
    const v = obj[k];
    arr[k] = v == null ? null : f ? Math.round(v * f) / f : v;
  }
  return arr;
};

window.__cumulativeAt = function (times, checkpoints) {
  const sorted = times.slice().sort((a, b) => a - b);
  const out = {};
  let i = 0;
  for (const c of checkpoints) {
    while (i < sorted.length && sorted[i] <= c) i++;
    out[c] = i;
  }
  return out;
};

window.__extractPerf = function (analysisJson, info, matchId) {
  const players = analysisJson.player || {};
  const adv = analysisJson.advanced_apm || {};
  const apmAgg = adv.player_apm_aggregations || {};
  const apmMax = apmAgg.max || {};
  const apmMean = apmAgg.mean || {};
  const uptimes = analysisJson.uptimes || {};
  const strategy = analysisJson.strategy || {};
  const market = analysisJson.market || {};
  const lobby = analysisJson.lobby || {};
  const unitMeta = (analysisJson.units && analysisJson.units.units) || {};
  const queuedUnits = (analysisJson.units && analysisJson.units.queued_units) || [];
  const techMeta = (analysisJson.techs && analysisJson.techs.techs) || {};
  const queuedTechs = (analysisJson.techs && analysisJson.techs.queued_techs) || [];
  const actions = (analysisJson.replay && analysisJson.replay.actions) || {};

  const vilTimes = {};
  const milTimes = {};
  const milByType = {};
  for (const e of queuedUnits) {
    const pid = String(e.player);
    const amt = e.amount || 1;
    if (window.__NON_MILITARY_UNIT_TYPES[e.unit_type]) {
      if (e.unit_type === 83) {
        vilTimes[pid] = vilTimes[pid] || [];
        for (let k = 0; k < amt; k++) vilTimes[pid].push(e.time);
      }
      continue;
    }
    milTimes[pid] = milTimes[pid] || [];
    for (let k = 0; k < amt; k++) milTimes[pid].push(e.time);
    const nm = unitMeta[e.unit_type] ? unitMeta[e.unit_type].name : "unit_" + e.unit_type;
    milByType[pid] = milByType[pid] || {};
    milByType[pid][nm] = (milByType[pid][nm] || 0) + amt;
  }

  const ecoTechs = {};
  const milTechs = {};
  for (const e of queuedTechs) {
    const pid = String(e.player);
    const meta = techMeta[e.tech_type];
    if (!meta) continue;
    const sec = Math.round(e.time / 1000);
    const bucket = meta.is_eco ? (ecoTechs[pid] = ecoTechs[pid] || []) : (milTechs[pid] = milTechs[pid] || []);
    bucket.push([meta.name, sec]);
  }

  const buildCount = {};
  const resignTime = {};
  for (const tkey of Object.keys(actions)) {
    for (const a of actions[tkey]) {
      const pid = String(a.player);
      if (a.type === "build") buildCount[pid] = (buildCount[pid] || 0) + 1;
      else if (a.type === "resign") resignTime[pid] = Math.round(Number(tkey) / 1000);
    }
  }

  const out = {
    _meta: {
      schema: window.__PERF_SCHEMA,
      match_id: matchId != null ? Number(matchId) : null,
      analysis_version: info && info.version != null ? info.version : null,
      duration_ms: analysisJson.duration != null ? analysisJson.duration : null,
      map: lobby.location || null,
      map_size: lobby.map_size || null,
      game_mode: lobby.game_mode || null,
      starting_age: lobby.starting_age || null,
      ending_age: lobby.ending_age || null,
      population: lobby.population != null ? lobby.population : null,
      victory: lobby.victory || null,
      ranked: lobby.ranked != null ? lobby.ranked : null,
    },
  };

  for (const pid of Object.keys(players)) {
    const p = players[pid];
    const vt = vilTimes[pid] || [];
    const mt = milTimes[pid] || [];
    const et = (ecoTechs[pid] || []).sort((a, b) => a[1] - b[1]);
    const mtq = (milTechs[pid] || []).sort((a, b) => a[1] - b[1]);
    const up = uptimes[pid] || {};
    const att = adv.attention_span && adv.attention_span[pid] ? adv.attention_span[pid] : {};
    out[pid] = {
      profile_id: p.profile_id,
      name: p.name,
      team: p.team,
      color: p.color != null ? p.color : null,
      civ: p.civilization ? p.civilization.label : null,
      is_ai: p.type !== "human",

      feudal_time_ms: up.feudal != null ? up.feudal : null,
      castle_time_ms: up.castle != null ? up.castle : null,
      imperial_time_ms: up.imperial != null ? up.imperial : null,
      opening: strategy[pid] || null,

      eapm_mean: apmAgg.total_mean ? (apmAgg.total_mean[pid] != null ? apmAgg.total_mean[pid] : null) : null,
      eapm_max: apmAgg.total_max ? (apmAgg.total_max[pid] != null ? apmAgg.total_max[pid] : null) : null,
      eapm_min: apmAgg.total_min ? (apmAgg.total_min[pid] != null ? apmAgg.total_min[pid] : null) : null,
      eco_apm_mean: apmMean[pid] ? (apmMean[pid].eco != null ? apmMean[pid].eco : null) : null,
      eco_apm_max: apmMax[pid] ? (apmMax[pid].eco != null ? apmMax[pid].eco : null) : null,
      military_apm_mean: apmMean[pid] ? (apmMean[pid].military != null ? apmMean[pid].military : null) : null,
      military_apm_max: apmMax[pid] ? (apmMax[pid].military != null ? apmMax[pid].military : null) : null,
      apm_per_min: window.__dictToMinuteArray(adv.player_apm ? adv.player_apm[pid] : null, 1),
      micro_ratio_over_time: window.__dictToMinuteArray(adv.micro_ratio_over_time ? adv.micro_ratio_over_time[pid] : null, 3),
      attention_span_ratio_over_time: window.__dictToMinuteArray(
        adv.attention_span_ratio_over_time ? adv.attention_span_ratio_over_time[pid] : null, 3
      ),
      attention_span_eco_ms: att.eco != null ? att.eco : null,
      attention_span_military_ms: att.military != null ? att.military : null,

      villagers_trained: vt.length,
      villager_count_at: window.__cumulativeAt(vt, window.__PERF_CHECKPOINTS_S),
      military_trained: mt.length,
      first_military_time_s: mt.length ? Math.round(Math.min.apply(null, mt)) : null,
      military_count_at: window.__cumulativeAt(mt, window.__PERF_CHECKPOINTS_S),
      military_by_type: milByType[pid] || {},

      eco_techs: et,
      military_techs: mtq,
      eco_tech_count: et.length,
      military_tech_count: mtq.length,

      market: market[pid] || null,
      buildings_built: buildCount[pid] || 0,
      resigned_time_s: resignTime[pid] != null ? resignTime[pid] : null,
    };
  }
  return out;
};
// ===================== end shared __extractPerf block ======================

window.__processMatch = async function (matchId, force) {
  if (!force && (window.__perfData[matchId] || window.__perfStatus[matchId])) return "skip";
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
        window.__perfData[matchId] = window.__extractPerf(data, info, matchId);
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

window.__processBatch = async function (matchIds, force) {
  for (const id of matchIds) {
    if (window.__rateLimited) break;
    try {
      await window.__processMatch(id, force);
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

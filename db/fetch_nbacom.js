// NBA-Predictor — descarga de stats.nba.com desde una pestaña de www.nba.com/stats (Claude in Chrome).
// Paso 1: navegar a https://www.nba.com/stats/teams/traditional
// Paso 2: ejecutar este archivo completo con javascript_tool (lanza la cola en segundo plano y vuelve enseguida).
// Paso 3: sondear `JSON.stringify(window.__nbaStatus)` cada ~30 s hasta que done=true (≈ 2 min, 54 peticiones).
// Paso 4: el script reemplaza el <body> por un <pre> con secciones "### <nombre>" -> leerlo con get_page_text
//         y guardarlo tal cual como db/raw/<fecha>/page_dump.txt; luego `python ingest_dump.py <fecha>`.
// IMPORTANTE: NO lanzar peticiones en paralelo. stats.nba.com bloquea la IP ~15 min si recibe ráfagas.
(() => {
  const SEASONS = window.__nbaSeasons || ['2026-27', '2025-26'];
  const H = { headers: { 'x-nba-stats-origin': 'stats', 'x-nba-stats-token': 'true' } };
  const base = 'https://stats.nba.com/stats/';
  const dash = (s, mt) => `${base}leaguedashteamstats?Conference=&DateFrom=&DateTo=&Division=&GameScope=&GameSegment=&Height=&ISTRound=&LastNGames=0&LeagueID=00&Location=&MeasureType=${encodeURIComponent(mt)}&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=PerGame&Period=0&PlayerExperience=&PlayerPosition=&PlusMinus=N&Rank=N&Season=${s}&SeasonSegment=&SeasonType=Regular%20Season&ShotClockRange=&StarterBench=&TeamID=0&TwoWay=0&VsConference=&VsDivision=`;
  const shots = (s, mt) => `${base}leaguedashteamshotlocations?Conference=&DateFrom=&DateTo=&DistanceRange=By%20Zone&Division=&GameScope=&GameSegment=&LastNGames=0&LeagueID=00&Location=&MeasureType=${mt}&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=PerGame&Period=0&PlayerExperience=&PlayerPosition=&PlusMinus=N&Rank=N&Season=${s}&SeasonSegment=&SeasonType=Regular%20Season&ShotClockRange=&StarterBench=&TeamID=0&VsConference=&VsDivision=`;
  const pt = (s, t, g) => `${base}synergyplaytypes?LeagueID=00&PerMode=PerGame&PlayType=${t}&PlayerOrTeam=T&SeasonType=Regular%20Season&SeasonYear=${s}&TypeGrouping=${g}`;
  const PT = ['Isolation', 'Transition', 'PRBallHandler', 'PRRollman', 'Postup', 'Spotup', 'Handoff', 'Cut', 'OffScreen', 'OffRebound'];
  const Z = ['Restricted Area', 'In The Paint (Non-RA)', 'Mid-Range', 'Corner 3', 'Above the Break 3'];
  const ZS = ['RA', 'PaintNonRA', 'MidRange', 'Corner3', 'ATB3'];
  const jobs = [];
  for (const s of SEASONS) {
    for (const mt of ['Base', 'Advanced', 'Misc', 'Opponent', 'Scoring']) jobs.push([`${s}|dash|${mt}`, dash(s, mt)]);
    jobs.push([`${s}|shots|Base`, shots(s, 'Base')], [`${s}|shots|Opponent`, shots(s, 'Opponent')]);
    for (const t of PT) for (const g of ['offensive', 'defensive']) jobs.push([`${s}|pt|${t}|${g}`, pt(s, t, g)]);
  }
  const R = {}; const st = window.__nbaStatus = { done: false, n: 0, total: jobs.length, errs: [] };
  const rnd = v => typeof v === 'number' ? Math.round(v * 10000) / 10000 : v;
  const sum = (rows, i) => Math.round(rows.reduce((a, l) => a + (+l.split(',')[i] || 0), 0) * 1000) / 1000;

  function exportAll() {
    const n2a = { 'LA Clippers': 'LAC' };
    for (const [k, rs] of Object.entries(R)) if (k.includes('|pt|')) {
      const h = rs.headers; rs.rowSet.forEach(r => { n2a[r[h.indexOf('TEAM_NAME')]] = r[h.indexOf('TEAM_ABBREVIATION')]; });
    }
    const out = {}; const cks = {};
    for (const s of SEASONS) {
      // métricas de equipo (ancho)
      const cols = ['TEAM_ABBREVIATION', 'GP']; const rows = {};
      for (const mt of ['Base', 'Advanced', 'Misc', 'Opponent', 'Scoring']) {
        const rs = R[`${s}|dash|${mt}`]; if (!rs) continue; const h = rs.headers;
        rs.rowSet.forEach(r => {
          const a = n2a[r[h.indexOf('TEAM_NAME')]] || r[h.indexOf('TEAM_NAME')];
          rows[a] = rows[a] || { TEAM_ABBREVIATION: a, GP: r[h.indexOf('GP')] };
          h.forEach((c, i) => {
            if (c.endsWith('_RANK') || ['TEAM_ID', 'TEAM_NAME', 'GP', 'W', 'L', 'W_PCT', 'MIN', 'CFID', 'CFPARAMS'].includes(c) || c.startsWith('E_') || c.startsWith('SP_WORK')) return;
            if (!(c in rows[a])) { rows[a][c] = rnd(r[i]); if (!cols.includes(c)) cols.push(c); }
          });
        });
      }
      const tl = Object.values(rows).map(o => cols.map(c => o[c] ?? '').join(','));
      out[`nbacom_team_${s}`] = [cols.join(',')].concat(tl).join('\n');
      cks[`nbacom_team_${s}`] = cols.slice(1).map((c, i) => sum(tl, i + 1));
      // play types (ancho: una fila por equipo y lado)
      const pr = {};
      for (const t of PT) for (const g of ['offensive', 'defensive']) {
        const rs = R[`${s}|pt|${t}|${g}`]; if (!rs) continue; const h = rs.headers; const side = g === 'offensive' ? 'off' : 'def';
        rs.rowSet.forEach(r => { const k = r[h.indexOf('TEAM_ABBREVIATION')] + ',' + side; pr[k] = pr[k] || {};
          pr[k][t + '_f'] = r[h.indexOf('POSS_PCT')]; pr[k][t + '_p'] = r[h.indexOf('PPP')]; });
      }
      const pc = PT.flatMap(t => [t + '_f', t + '_p']);
      const pl = Object.keys(pr).sort().map(k => k + ',' + pc.map(c => pr[k][c] ?? '').join(','));
      out[`ptw_${s}`] = ['team,side,' + pc.join(',')].concat(pl).join('\n');
      cks[`ptw_${s}`] = pc.map((c, i) => sum(pl, i + 2));
      // tiros por zona (ancho)
      const sl = [];
      for (const [mt, side] of [['Base', 'off'], ['Opponent', 'def']]) {
        const rs = R[`${s}|shots|${mt}`]; if (!rs) continue; const zones = rs.headers[0].columnNames;
        rs.rowSet.forEach(r => { const a = n2a[r[1]] || r[1];
          const vals = Z.flatMap(z => { const b = 2 + zones.indexOf(z) * 3; return [r[b + 1], r[b + 2]]; });
          sl.push(a + ',' + side + ',' + vals.join(',')); });
      }
      sl.sort();
      out[`shw_${s}`] = ['team,side,' + ZS.flatMap(z => [z + '_fga', z + '_pct']).join(',')].concat(sl).join('\n');
      cks[`shw_${s}`] = ZS.flatMap((z, zi) => [sum(sl, 2 + zi * 2), sum(sl, 3 + zi * 2)]);
    }
    window.__nbaExport = out;
    document.body.innerHTML = '';
    const p = document.createElement('pre');
    p.textContent = Object.entries(out).map(([k, v]) => '### ' + k + '\n' + v).join('\n') +
      '\n### CHECKSUMS\n' + JSON.stringify(cks) + '\n### END';
    document.body.appendChild(p);
  }

  (async () => {
    for (const [k, u] of jobs) {
      let ok = false;
      for (let a = 0; a < 2 && !ok; a++) {
        try {
          const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), 25000);
          const r = await fetch(u, { ...H, signal: ctl.signal }); clearTimeout(to);
          const j = await r.json(); R[k] = Array.isArray(j.resultSets) ? j.resultSets[0] : j.resultSets; ok = true;
        } catch (e) { if (a === 1) st.errs.push(k + ': ' + e); await new Promise(r => setTimeout(r, 5000)); }
      }
      st.n++;
      await new Promise(r => setTimeout(r, 1500));
    }
    try { exportAll(); } catch (e) { st.errs.push('export: ' + e); }
    st.done = true;
  })();
  return 'cola lanzada: ' + jobs.length + ' peticiones';
})();

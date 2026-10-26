"""
Convierte el volcado de fetch_nbacom.js en los CSV del snapshot, verifica sumas de control,
y reconstruye la BD.

Uso:
    python ingest_dump.py 2026-10-28            # lee raw/2026-10-28/page_dump.txt
    python ingest_dump.py 2026-10-28 --no-build

Qué hace:
 1. Separa las secciones "### nombre" del volcado -> nbacom_team_<s>.csv, ptw_<s>.csv, shw_<s>.csv
 2. Compara la suma de cada columna con la sección CHECKSUMS (detecta errores de copia). Si algo no
    cuadra, se detiene sin tocar la BD.
 3. Pasa play types y zonas de tiro a formato largo (nbacom_playtypes_<s>.csv, nbacom_shots_<s>.csv)
 4. Si el snapshot no trae dvp_statpick.csv, copia el del snapshot anterior y lo anota en meta.json
 5. Ejecuta build_db.py
"""
import glob, json, os, shutil, subprocess, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PT = ['Isolation', 'Transition', 'PRBallHandler', 'PRRollman', 'Postup', 'Spotup', 'Handoff', 'Cut', 'OffScreen', 'OffRebound']
ZS = ['RA', 'PaintNonRA', 'MidRange', 'Corner3', 'ATB3']


def split_sections(text):
    secs, cur = {}, None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith('### '):
            cur = line[4:].strip()
            secs[cur] = []
        elif cur and line:
            secs[cur].append(line)
    return secs


def main(date, build=True):
    snap = os.path.join(HERE, 'raw', date)
    secs = split_sections(open(os.path.join(snap, 'page_dump.txt'), encoding='utf-8').read())
    cks = json.loads(secs.pop('CHECKSUMS')[0])
    secs.pop('END', None)
    bad = []
    for name, lines in secs.items():
        path = os.path.join(snap, name + '.csv')
        open(path, 'w').write('\n'.join(lines) + '\n')
        d = pd.read_csv(path)
        first = 2 if name.startswith(('ptw_', 'shw_')) else 1
        for c, v in zip(d.columns[first:], cks[name]):
            if abs(pd.to_numeric(d[c], errors='coerce').fillna(0).sum() - v) > 0.01:
                bad.append(f'{name}.{c}: {d[c].sum():.3f} != {v}')
        exp_rows = 60 if first == 2 else 30
        if len(d) != exp_rows:
            bad.append(f'{name}: {len(d)} filas (esperadas {exp_rows})')
    if bad:
        print('ERROR en sumas de control — revisar page_dump.txt:\n  ' + '\n  '.join(bad))
        sys.exit(1)
    print(f'Sumas de control OK ({len(secs)} tablas)')

    seasons = sorted({n.split('_')[-1] for n in secs})
    for s in seasons:
        w = pd.read_csv(os.path.join(snap, f'ptw_{s}.csv'))
        L = pd.DataFrame([dict(team=r.team, play_type=p, side=r.side, poss_pct=r[p + '_f'], ppp=r[p + '_p'])
                          for _, r in w.iterrows() for p in PT])
        for c in ['percentile', 'poss', 'pts', 'efg', 'tov_pct', 'score_pct']:
            L[c] = np.nan
        L.to_csv(os.path.join(snap, f'nbacom_playtypes_{s}.csv'), index=False)
        w = pd.read_csv(os.path.join(snap, f'shw_{s}.csv'))
        pd.DataFrame([dict(team=r.team, side=r.side, zone=z, fgm=round(r[z + '_fga'] * r[z + '_pct'], 2),
                           fga=r[z + '_fga'], fg_pct=r[z + '_pct'])
                      for _, r in w.iterrows() for z in ZS]).to_csv(os.path.join(snap, f'nbacom_shots_{s}.csv'), index=False)

    # meta + DvP heredado si falta
    prev = sorted(p for p in glob.glob(os.path.join(HERE, 'raw', '*')) if os.path.basename(p) < date)
    meta_p = os.path.join(snap, 'meta.json')
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else (
        json.load(open(os.path.join(prev[-1], 'meta.json'))) if prev else {})
    meta['season'] = max(seasons)
    meta['prior_season'] = min(seasons) if len(seasons) > 1 else None
    if not os.path.exists(os.path.join(snap, 'dvp_statpick.csv')):
        src = next((p for p in reversed(prev) if os.path.exists(os.path.join(p, 'dvp_statpick.csv'))), None)
        if src:
            shutil.copy(os.path.join(src, 'dvp_statpick.csv'), snap)
            meta['notes'] = (meta.get('notes') or '') + f' DvP heredado de {os.path.basename(src)}.'
    meta['cutoff'] = f'NBA.com consultado {date}'
    json.dump(meta, open(meta_p, 'w'), ensure_ascii=False, indent=1)

    if build:
        subprocess.run([sys.executable, os.path.join(HERE, 'build_db.py')], check=True)


if __name__ == '__main__':
    main(sys.argv[1], build='--no-build' not in sys.argv)

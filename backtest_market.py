"""
Backtest del modelo 1.5 (1-nov): con las líneas ya liquidadas de docs/data (las que tienen cuota a los dos lados),
qué habría pasado con la mezcla con la casa, el tope por jugador / jornada, sin picks de equipo y sin Unders de
rookies. Uso: python backtest_market.py
"""
import json,glob,os,collections,math,sys
sys.path.insert(0,'.')
from evaluate import norm
pl=json.load(open('players/2026-27.json'))
flagged={norm(x) for x in pl['rookies']}|{norm(x) for x in pl['missed_prior']}
allb=[]
for f in sorted(glob.glob('docs/data/2026-*.json')):
    d=json.load(open(f))
    for b in d['bets']:
        if b.get('result') in ('won','lost'): allb.append(dict(b,gid=d['id'],date=d['id'][:10]))
grp=collections.defaultdict(list)
for b in allb: grp[(b['gid'],b['kind'],b.get('player'),b.get('stat'),b.get('line'))if b['kind']=='prop' else (b['gid'],b['kind'])].append(b)
P=[]
for v in grp.values():
    if len(v)==2:
        i0,i1=1/v[0]['dec'],1/v[1]['dec'];s=i0+i1
        for b,i in ((v[0],i0),(v[1],i1)): P.append(dict(b,pm=i/s))
def side(b): return b.get('side') or ('over' if ' Over ' in b['label'] else 'under' if ' Under ' in b['label'] else '-')
REDUND={'pts':'p','pr':'p','pa':'p','pra':'p','reb':'r','ra':'r','ast':'a','stl':'d','blk':'d','stocks':'d'}
def run(w=0.25,cap=0.15,team=False,rook=True,perplayer=2,daily=15,ev=0.03,name=''):
    sel=[]
    for b in P:
        if not team and b['kind']!='prop': continue
        gap=b['p']-b['pm']
        if cap is not None and abs(gap)>cap: continue
        if rook and b['kind']=='prop' and norm(b['player']) in flagged and side(b)=='under': continue
        q=b['pm']+w*gap; e=q*b['dec']-1
        if e>=ev: sel.append(dict(b,q=q,e=e))
    out=[]
    for dte in sorted({b['date'] for b in sel}):
        day=sorted([b for b in sel if b['date']==dte],key=lambda b:-b['e'])
        cnt=collections.Counter(); fam=set(); keep=[]
        for b in day:
            k=(b['gid'],b.get('player') or b.get('team'))
            if perplayer and b['kind']=='prop':
                if cnt[k]>=perplayer: continue
                fk=(k,REDUND.get(b.get('stat'),b.get('stat')))
                if fk in fam: continue
                fam.add(fk)
            cnt[k]+=1; keep.append(b)
            if daily and len(keep)>=daily: break
        out+=keep
    n=len(out);w_=sum(b['result']=='won' for b in out);u=sum((b['dec']-1) if b['result']=='won' else -1 for b in out)
    pq=sum(b['q'] for b in out)/max(n,1); imp=sum(1/b['dec'] for b in out)/max(n,1)
    bd=collections.OrderedDict()
    for dte in sorted({b['date'] for b in out}):
        x=[b for b in out if b['date']==dte]; bd[dte[5:]]=(len(x),round(sum((b['dec']-1) if b['result']=='won' else -1 for b in x),1))
    print(f"{name:<38} n={n:<4} {w_}-{n-w_} hit={w_/max(n,1):.3f} q={pq:.3f} impl={imp:.3f} u={u:+.1f} roi={u/max(n,1):+.1%}  {dict(bd)}")
    return out
print('paired settled lines',len(P))
run(w=1,cap=None,team=True,rook=False,perplayer=0,daily=0,name='actual (modelo solo, sin limites)')
run(w=1,cap=None,team=False,rook=False,perplayer=0,daily=0,name='solo 5 (sin equipo)')
run(w=1,cap=None,team=True,rook=False,perplayer=2,daily=15,name='solo 4 (limites)')
run(w=1,cap=None,team=True,rook=True,perplayer=0,daily=0,name='solo 7 (rookies)')
run(w=1,cap=0.15,team=True,rook=False,perplayer=0,daily=0,name='solo 2 (cap 15pp)')
run(w=0.25,cap=None,team=True,rook=False,perplayer=0,daily=0,name='solo 1 (mezcla 0.25)')
for w in (0.15,0.25,0.35,0.5):
  for cap in (0.15,0.20,None):
    run(w=w,cap=cap,name=f'todos w={w} cap={cap}')
run(w=0.25,cap=0.15,daily=0,name='todos w=.25 cap=.15 sin tope diario')
o=run(w=0.25,cap=None,name='modelo 1.5 (1+4+5+7)')
print(collections.Counter((b.get('stat'),side(b)) for b in o).most_common(8))

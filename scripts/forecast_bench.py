#!/usr/bin/env python3
"""
Forecast benchmark: v2.2 routed forecaster vs the v2.0 rolling-mean baseline.

Walk-forward one-step evaluation on real M5 data, held-out tail. This is the
script that produced the routing table in core/forecast.py:auto_select and the
numbers in docs/HANDOFF.md section 9.1. Re-run it after ANY forecaster change:

    cd services/smartstock && python3 ../../scripts/forecast_bench.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services", "smartstock"))
import numpy as np, pandas as pd
from smartstock.core.forecast import forecast_for, MovingAverage
from smartstock.core.segmentation import classify_demand
df=pd.read_csv('/sessions/amazing-blissful-clarke/mnt/AI/erp-smartstock/demo/data/m5_multi_sku.csv')
skus=sorted(df.sku_id.unique()); H=120
S={s:df[df.sku_id==s].sort_values('day') for s in skus}
L=min(len(v) for v in S.values())
def run(mode):
    by={}; tot=[0.,0.,0]
    for s in skus:
        g=S[s].iloc[:L]; y=g.demand.to_numpy(float)
        dow=g.dow.to_numpy(int); snap=g.snap.astype(str).str.lower().eq('true').to_numpy(int)
        cls,_,_=classify_demand(y[:-H]); ae=se=k=0.
        mv=MovingAverage(28)
        for t in range(L-H,L):
            if mode=='v20': mu,_=mv.predict(y[:t],horizon=1)
            else:
                cal={'dow':dow[:t],'next_dow':int(dow[t]),'snap':snap[:t],'next_snap':int(snap[t])}
                mu,_,_=forecast_for(y[:t],horizon=1,calendar=cal)
            e=y[t]-mu; ae+=abs(e); se+=e*e; k+=1
        b=by.setdefault(cls.value,[0.,0]); b[0]+=ae; b[1]+=k
        tot[0]+=ae; tot[1]+=se; tot[2]+=k
    return {c:v[0]/v[1] for c,v in by.items()}, tot[0]/tot[2], (tot[1]/tot[2])**.5
a,amae,armse=run('v20'); b,bmae,brmse=run('v22')
print(f"FINAL v2.2 forecaster vs v2.0 rolling mean  ({len(skus)} SKUs x {H} days)\n")
print(f"{'class':<15}{'v2.0 rolling':>14}{'v2.2 routed':>14}{'delta':>10}")
print("-"*53)
for c in sorted(a): print(f"{c:<15}{a[c]:>14.3f}{b[c]:>14.3f}{(1-b[c]/a[c])*100:>9.1f}%")
print("-"*53)
print(f"{'OVERALL MAE':<15}{amae:>14.3f}{bmae:>14.3f}{(1-bmae/amae)*100:>9.1f}%")
print(f"{'OVERALL RMSE':<15}{armse:>14.3f}{brmse:>14.3f}{(1-brmse/armse)*100:>9.1f}%")

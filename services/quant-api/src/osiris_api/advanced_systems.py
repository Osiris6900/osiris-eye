"""Research-only primitives for OSIRIS advanced systems 9-20."""
from math import isfinite, sqrt
from statistics import mean

def vals(xs, name, n=1):
    out=[float(x) for x in xs]
    if len(out)<n or not all(isfinite(x) for x in out): raise ValueError(f"{name} requires {n}+ finite values")
    return out

def order_book(bids, asks):
    def side(rows):
        out=[(float(x["price"]),float(x["quantity"])) for x in rows]
        if not out or any(not isfinite(p+q) or p<=0 or q<0 for p,q in out): raise ValueError("invalid book levels")
        return out
    b,a=side(bids),side(asks); bp=max(p for p,q in b); ap=min(p for p,q in a)
    if bp>=ap: raise ValueError("crossed or locked book")
    bd=sum(q for p,q in b); ad=sum(q for p,q in a); mid=(bp+ap)/2
    return {"best_bid":bp,"best_ask":ap,"spread_bps":(ap-bp)/mid*10000,"depth_imbalance":(bd-ad)/(bd+ad) if bd+ad else 0,"bid_depth":bd,"ask_depth":ad}

def execution_sim(quantity, arrival_price, fills, fee_bps=0):
    q,p,f=float(quantity),float(arrival_price),float(fee_bps)
    if not all(isfinite(x) for x in (q,p,f)) or q<=0 or p<=0 or f<0: raise ValueError("invalid order inputs")
    done=notional=0.0
    for x in fills:
        fq,fp=float(x["quantity"]),float(x["price"])
        if not isfinite(fq+fp) or fq<0 or fp<=0 or done+fq>q+1e-9: raise ValueError("invalid or excess fills")
        done+=fq; notional+=fq*fp
    avg=notional/done if done else None
    return {"filled_quantity":done,"fill_ratio":done/q,"average_fill_price":avg,"implementation_shortfall_bps":((avg-p)/p*10000 if avg else None),"estimated_fees":notional*f/10000,"unfilled_quantity":q-done,"live_execution":False}

def model_governance(reference,current,threshold=1):
    r=vals(reference,"reference",2); c=vals(current,"current",2); t=float(threshold)
    if not isfinite(t) or t<0: raise ValueError("threshold must be non-negative")
    m=mean(r); sd=sqrt(sum((x-m)**2 for x in r)/len(r)); shift=abs(mean(c)-m)/(sd if sd>1e-12 else 1)
    return {"normalized_mean_shift":shift,"status":"review_required" if shift>t else "within_threshold","deployment_authorized":False}

def knowledge_graph(nodes,edges):
    ids=[str(x.get("id","")) for x in nodes]
    if any(not x for x in ids) or len(ids)!=len(set(ids)): raise ValueError("node ids must be unique and non-empty")
    known=set(ids); bad=[i for i,e in enumerate(edges) if e.get("source") not in known or e.get("target") not in known or not e.get("relation")]
    return {"nodes":len(nodes),"edges":len(edges),"invalid_edge_indexes":bad,"integrity":"degraded" if bad else "valid","causality_validated":False}

def strategy_discovery(signals,returns):
    r=vals(returns,"returns",3); out=[]
    for i,s in enumerate(signals):
        x=vals(s,f"signal {i}",3)
        if len(x)!=len(r): raise ValueError("signals and returns must align")
        p=[x[j-1]*r[j] for j in range(1,len(r))]; m=mean(p); sd=sqrt(sum((v-m)**2 for v in p)/len(p))
        out.append({"candidate":i,"mean_return":m,"period_sharpe":m/sd if sd else 0,"observations":len(p)})
    return {"candidates":out,"multiple_testing_adjusted":False,"out_of_sample_validated":False}

def regime_change(values,window=20,threshold=2):
    x=vals(values,"values",4); w=int(window); t=float(threshold)
    if w<2 or len(x)<2*w or not isfinite(t) or t<=0: raise ValueError("insufficient windows or invalid threshold")
    a,b=x[-2*w:-w],x[-w:]; m=mean(a); sd=sqrt(sum((v-m)**2 for v in a)/len(a)); z=(mean(b)-m)/(sd if sd>1e-12 else 1)
    return {"shift_z":z,"change_flag":abs(z)>=t,"method":"two_window_heuristic"}

def monte_carlo(mu,vol,periods=20,paths=1000,seed=7):
    import random
    mu,vol=float(mu),float(vol); n,k=int(periods),int(paths)
    if not isfinite(mu+vol) or vol<0 or n<1 or k<10 or k>100000: raise ValueError("invalid simulation parameters")
    rng=random.Random(int(seed)); out=[]
    for _ in range(k):
        e=1.0
        for _ in range(n):
            r=rng.gauss(mu,vol)
            if r<=-1: e=0; break
            e*=1+r
        out.append(e-1)
    out.sort()
    return {"paths":k,"periods":n,"mean":mean(out),"p05":out[int(.05*(k-1))],"median":out[k//2],"p95":out[int(.95*(k-1))],"assumption":"iid_gaussian","forecast_or_guarantee":False}

def news_audit(items):
    issues=[]
    for i,x in enumerate(items):
        if not all(x.get(k) for k in ("source","published_at","event_time","headline")): issues.append({"index":i,"code":"missing_provenance"})
        try:
            s=float(x.get("sentiment",0))
            if not isfinite(s) or not -1<=s<=1: raise ValueError()
        except (ValueError,TypeError): issues.append({"index":i,"code":"invalid_sentiment"})
    return {"received":len(items),"issues":issues,"sentiment_is_signal":False}

def security_audit(permissions):
    allowed={"read_market_data","read_research","write_research","approve_release"}; issues=[]
    for p in permissions:
        grants=set(p.get("permissions",[])); role=p.get("role","")
        if grants-allowed: issues.append({"role":role,"code":"unknown_permission"})
        if "approve_release" in grants and role not in {"human_reviewer","administrator"}: issues.append({"role":role,"code":"privileged_grant"})
    return {"issues":issues,"status":"review_required" if issues else "syntactically_valid","authorization_enforced":False}

def attribution(asset_returns,weights):
    if not asset_returns or set(asset_returns)!=set(weights): raise ValueError("asset keys must match")
    rows={k:vals(v,k) for k,v in asset_returns.items()}
    if len({len(v) for v in rows.values()})!=1: raise ValueError("returns must align")
    w={k:float(v) for k,v in weights.items()}
    if not all(isfinite(v) for v in w.values()) or abs(sum(w.values())-1)>1e-6: raise ValueError("weights must sum to one")
    c={k:w[k]*mean(rows[k]) for k in rows}
    return {"contributions":c,"portfolio_mean_return":sum(c.values()),"method":"arithmetic_contribution"}

def compute_plan(tasks,workers):
    n,w=int(tasks),int(workers)
    if n<0 or w<1 or w>10000: raise ValueError("invalid task/worker count")
    return {"tasks":n,"workers":w,"tasks_per_worker_ceiling":(n+w-1)//w,"distributed_execution":"not_started","runtime_measured":False}

def incident_assessment(checks):
    if not checks: raise ValueError("checks required")
    allowed={"healthy","degraded","down","unknown"}; incidents=[]
    for c in checks:
        if c.get("status") not in allowed: raise ValueError("invalid component status")
        if c["status"]!="healthy": incidents.append({"component":c.get("component","unknown"),"status":c["status"],"action":"block_new_risk" if c.get("critical",True) else "alert"})
    return {"overall":"incident" if incidents else "healthy","incidents":incidents,"new_risk_allowed":not any(x["action"]=="block_new_risk" for x in incidents),"automated_remediation":False}

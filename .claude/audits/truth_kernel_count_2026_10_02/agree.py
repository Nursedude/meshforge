import json, collections, sys
S=sys.argv[1]
r1=[json.loads(l) for l in open(S+'/rater1.jsonl')]; r2=[json.loads(l) for l in open(S+'/rater2.jsonl')]
assert [a['id'] for a in r1]==[b['id'] for b in r2]
def kappa(x,y):
    n=len(x); po=sum(a==b for a,b in zip(x,y))/n
    cx=collections.Counter(x); cy=collections.Counter(y)
    pe=sum(cx[k]*cy[k] for k in set(x)|set(y))/n/n
    return po,(po-pe)/(1-pe) if pe<1 else float('nan')
po,k=kappa([a['class'] for a in r1],[b['class'] for b in r2]); print(f"primary class: agree {po:.0%} kappa {k:.2f} (all 164)")
both=[(a,b) for a,b in zip(r1,r2) if a['class']!='NA' and b['class']!='NA']
print(f"units both rated non-NA: {len(both)}")
for f in ['F_SHARED','F_TRISTATE','F_RUST']:
    po,k=kappa([a[f] for a,b in both],[b[f] for a,b in both])
    s1=sum(a[f] for a,b in both)/len(both); s2=sum(b[f] for a,b in both)/len(both)
    both_t=sum(a[f] and b[f] for a,b in both)
    print(f"{f}: r1 {s1:.0%} r2 {s2:.0%} mean {(s1+s2)/2:.0%} | both-true {both_t} | agree {po:.0%} kappa {k:.2f}")
for c in ['C1','C2','C3','C4','C5']:
    s1=sum(a['class']==c for a,b in both)/len(both); s2=sum(b['class']==c for a,b in both)/len(both)
    print(f"{c}: r1 {s1:.0%} r2 {s2:.0%} mean {(s1+s2)/2:.0%}")
po,k=kappa([a['class'] for a,b in both],[b['class'] for a,b in both]); print(f"primary on both-non-NA: agree {po:.0%} kappa {k:.2f}")
json.dump([a['id'] for a,b in both if a['F_SHARED'] and b['F_SHARED']], open(S+'/shared_both.json','w'))

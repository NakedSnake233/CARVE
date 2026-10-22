#!/usr/bin/env python3
"""Standalone descriptive figures from the registered native suite's raw reports."""
import argparse
import json
from pathlib import Path
import statistics
import os
os.environ.setdefault('MPLCONFIGDIR','/tmp/rca2remed-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def draw(source,out):
    report=json.loads(Path(source).read_text());out=Path(out);out.mkdir(parents=True,exist_ok=False)
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    def save(fig,name):
        fig.tight_layout();fig.savefig(out/(name+'.png'),dpi=180);fig.savefig(out/(name+'.pdf'));plt.close(fig)
    names=['main','no_edges','no_distinguish','diagnosis','state','full_graph']
    fig,axes=plt.subplots(1,2,figsize=(12,4))
    for i,name in enumerate(names):
        rows=[r for r in report['per_run'] if r['view']=='base' and r['name']==name]
        for ax,key in zip(axes,['business_brier','mean_retained_nodes']):
            values=[r[key] for r in rows if r[key] is not None]
            if values:
                ax.scatter([i]*len(values),values,s=28,alpha=.65)
                ax.plot([i-.18,i+.18],[statistics.mean(values)]*2,color='black',linewidth=2)
    for ax in axes:ax.set_xticks(range(len(names)),names,rotation=25,ha='right')
    axes[0].set_ylabel('Business-recovery Brier (known attempts)');axes[1].set_ylabel('Mean retained graph nodes')
    fig.suptitle('Native held-out replay: three fixed seeds; bars mark means')
    save(fig,'main_prediction_and_size')
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for strategy in ['fixed','random','decision']:
        for axis,key in zip(axes,['mean_calls','mean_registered_cost']):
            xs=[];ys=[]
            for limit in [1,2,3]:
                values=[r['actual_resources'][key] for r in report['refinement'] if r['strategy']==strategy and r['limits']['max_calls']==limit]
                xs.append(limit);ys.append(statistics.mean(v for v in values if v is not None))
            axis.plot(xs,ys,marker='o',label=strategy)
    for ax in axes:ax.set_xlabel('Allowed query calls');ax.set_xticks([1,2,3]);ax.legend()
    axes[0].set_ylabel('Actual calls per condition');axes[1].set_ylabel('Registered query cost per condition')
    fig.suptitle('Saved pre-action observation replay: actual spend under common limits')
    save(fig,'refinement_actual_budget')
    fig,axes=plt.subplots(1,2,figsize=(12,5))
    for ax,key in zip(axes,['top_level_timings','nested_execution_timings']):
        timings=report['cost_failure'][key];items=[(k,v['mean']) for k,v in timings.items() if k!='total_attempt']
        ax.barh([x[0] for x in items],[x[1] for x in items]);ax.set_xlabel('Mean recorded seconds');ax.set_title(key.replace('_',' '))
    fig.suptitle('Timing scopes overlap: do not sum across or within parent/child phases')
    save(fig,'recorded_phase_costs')
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for dimension in ['services','dependencies','telemetry']:
        rows=[r for r in report['scaling']['records'] if r['dimension']==dimension]
        axes[0].plot([r['factor'] for r in rows],[r['mean_predictor_seconds']*1000 for r in rows],marker='o',label=dimension)
    rows=[r for r in report['scaling']['records'] if r['dimension']=='concurrent_replays']
    axes[1].plot([r['factor'] for r in rows],[statistics.mean(v['replays_per_second'] for v in r['measurements']) for r in rows],marker='o')
    axes[0].set_ylabel('Predictor including tensorization (ms)');axes[0].legend();axes[1].set_ylabel('Replays per second')
    axes[0].set_xlabel('Validation graph augmentation factor');axes[1].set_xlabel('Concurrent replay calls')
    fig.suptitle('Offline compute scaling; no deployed-application efficacy claim')
    save(fig,'offline_scaling')
    (out/'PROVENANCE.json').write_text(json.dumps({'source':str(Path(source).resolve()),'scope':'Descriptive display only; metrics are not transformed beyond time units and seed means','figures':sorted(p.name for p in out.glob('*.pdf'))},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--results',required=True,type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args();draw(a.results,a.out)

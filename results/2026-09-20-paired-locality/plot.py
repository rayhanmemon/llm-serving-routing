"""Recreate the measured paired-difference plot from summary.json (matplotlib)."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE=Path(__file__).resolve().parent
data=json.loads((HERE/'summary.json').read_text())
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(1,2,figsize=(11,5),sharey=True)
for ax,tokens in zip(axes,(512,8192)):
 for i,load in enumerate((0,1,3)):
  group=next(g for g in data['groups'] if g['background_requests']==load and g['prompt_tokens']==tokens)
  rows=group['pairs'];n=len(rows)
  for j,row in enumerate(rows):
   ax.scatter(i+(j-(n-1)/2)*.025,row['remote_minus_local_ms'],s=35,
              color='#1672b8' if row['first']=='pd-local' else '#d77b18',alpha=.85,zorder=3)
  mean=group['mean_remote_minus_local_ms'];lo,hi=group['exploratory_paired_bootstrap_95pct_ms']
  ax.errorbar(i,mean,yerr=[[mean-lo],[hi-mean]],fmt='ks',capsize=5,markersize=5,zorder=4)
 ax.axhline(0,color='#333333',linestyle='--',linewidth=1)
 ax.set_xticks([0,1,2],['0 (12 pairs)','1 (6 pairs)','3 (6 pairs)'])
 ax.set_xlabel('Background requests on the local decoder')
 ax.set_title(f'{tokens:,}-token prompt')
 ax.set_xlim(-.4,2.4);ax.set_ylim(-32,27);ax.grid(axis='y',alpha=.2)
axes[0].set_ylabel('Remote TTFT − local TTFT (ms)\nPositive = local faster')
fig.suptitle('Verified NVLink locally / RDMA remotely: client latency differences',fontsize=14,y=.98)
fig.legend(handles=[Line2D([],[],marker='o',linestyle='',color='#1672b8',label='Local request first'),Line2D([],[],marker='o',linestyle='',color='#d77b18',label='Remote request first'),Line2D([],[],marker='s',color='black',label='Paired mean + exploratory 95% bootstrap interval')],loc='lower center',bbox_to_anchor=(.5,.06),ncol=3,frameon=False,fontsize=9)
fig.text(.5,.025,'Qwen3-0.6B · H200 · 32 output tokens · fixed routes; no router-policy comparison',ha='center',fontsize=9,color='#444444')
fig.tight_layout(rect=[0,.14,1,.94]);fig.savefig(HERE/'paired-ttft.png',dpi=180)

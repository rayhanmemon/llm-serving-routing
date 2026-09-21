"""Render the recorded timing and transfer-submission results."""
import json
from pathlib import Path
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent
summary=json.loads((HERE/'summary.json').read_text())
components=json.loads((HERE/'component-analysis.json').read_text())
lengths=[4096,32768,65536,122880];labels=['4K','32K','64K','120K']
fig,axes=plt.subplots(1,2,figsize=(13,4.8),gridspec_kw={'width_ratios':[1.15,1]})
fig.patch.set_facecolor('#fafbfc')
colors={'local':'#2563a6','remote':'#c96a24'}
for ax in axes:
    ax.set_facecolor('#fafbfc');ax.spines[['top','right']].set_visible(False)
    ax.grid(alpha=.16);ax.set_axisbelow(True)
ax=axes[0]
for i,length in enumerate(lengths):
    rows=[r for r in summary['pairs'] if r['input_tokens']==length]
    for j,row in enumerate(rows):
        ax.scatter(row['remote_minus_local_ms'],i+(j-5.5)*.035,s=27,
                   color=colors[row['first']],alpha=.8,label=f"{row['first'].capitalize()} first" if i==0 and j<2 else None)
    group=next(g for g in summary['groups'] if g['input_tokens']==length)
    ax.scatter(group['paired_mean_remote_minus_local_ms'],i,marker='D',s=45,color='#172b3a',zorder=5)
ax.axvline(0,color='#53606c',lw=1)
ax.set_yticks(range(4),labels);ax.invert_yaxis()
ax.set_xlabel('Remote minus local client TTFT (ms)')
ax.set_ylabel('Input tokens (K = 1,024)')
ax.set_title('Remote had lower mean TTFT at every length',loc='left',fontsize=11,fontweight='bold')
ax.text(.01,-.24,'Negative favors remote. Diamonds show paired means.\nEach dot is one matched pair; route order is balanced.',transform=ax.transAxes,fontsize=9,color='#53606c')
from matplotlib.lines import Line2D
ax.legend(handles=[Line2D([0],[0],marker='o',linestyle='',color=colors[r],label=r.capitalize()+' first') for r in colors],fontsize=8,loc='upper left')
ax=axes[1]
for route in ['local','remote']:
    rows=[next(g for g in components['groups'] if g['input_tokens']==n and g['route']==route) for n in lengths]
    ax.plot(range(4),[g['transfer_ms_per_rank'] for g in rows],marker='o',color=colors[route],label=route.capitalize()+' total transfer')
    ax.plot(range(4),[g['post_ms_per_rank'] for g in rows],marker='s',linestyle='--',color=colors[route],label=route.capitalize()+' submission')
ax.set_xticks(range(4),labels);ax.set_ylim(bottom=0)
ax.set_ylabel('Mean transfer time per GPU worker (ms)')
ax.set_xlabel('Input tokens')
ax.set_title('Local submission cost dominated the transfer',loc='left',fontsize=11,fontweight='bold')
ax.legend(fontsize=8,loc='upper left')
ax.text(.01,-.24,'Submission is included in total transfer, not added to it.\nThese are per-rank metrics, not pure link-bandwidth measurements.',transform=ax.transAxes,fontsize=9,color='#53606c')
fig.suptitle('Qwen3-32B · TP4 (four GPUs per engine) · default cache layout',fontsize=14,fontweight='bold',x=.07,ha='left')
fig.subplots_adjust(left=.075,right=.985,top=.83,bottom=.26,wspace=.32)
fig.savefig(HERE/'tp4-locality.png',dpi=180,facecolor=fig.get_facecolor())

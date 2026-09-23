"""Conservative staged rental accounting; no cloud calls."""
from decimal import Decimal
import math

GPU_RATE=Decimal('19.60')
SUPPORT_RATE=Decimal('0.50')
CAP=Decimal('75')
MARGIN=Decimal('3')
CLEANUP_SECONDS=1200


def cost(start,local,remote,end):
    result=SUPPORT_RATE*Decimal(str(max(0,end-start)))/3600
    for born in (local,remote):
        if born is not None:result+=GPU_RATE*Decimal(str(max(0,end-born)))/3600
    return result


def spending_cap(remaining):
    value=Decimal(str(remaining))
    if not value.is_finite() or value < Decimal('74'):
        raise ValueError('Insufficient remaining budget for the prepared full comparison')
    return min(CAP,value)


def deadlines(start,local,remote,now,original_target,cap=CAP):
    """Treat requested node groups as fully billed; never extend the original target."""
    spent=cost(start,local,remote,now)
    rate=SUPPORT_RATE+GPU_RATE*sum(x is not None for x in (local,remote))
    cap=spending_cap(cap)
    left=cap-MARGIN-spent
    if left<=0:raise ValueError('Session spending reserve exhausted')
    target=math.floor(min(original_target,now+float(left/rate*3600)))
    return {'cleanup_start_deadline_unix':target-CLEANUP_SECONDS,'deletion_target_unix':target,
            'projected_cost_usd':str(cost(start,local,remote,target)), 'session_cap_usd':str(cap),
            'local_requested_unix':local,'remote_requested_unix':remote}


def admit_remote(start,local,now,original_target,required_work_seconds=76*60,cap=CAP):
    result=deadlines(start,local,now,now,original_target,cap=cap)
    if result['cleanup_start_deadline_unix']-now < required_work_seconds:
        raise ValueError('Insufficient funded time for remote startup, minimum comparison and cleanup')
    return result

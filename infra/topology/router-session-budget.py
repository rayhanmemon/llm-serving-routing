"""Conservative staged rental accounting; no cloud calls."""
from decimal import Decimal
import math

GPU_RATE=Decimal('19.60')
SUPPORT_RATE=Decimal('0.50')
CAP=Decimal('70')
MARGIN=Decimal('3')
CLEANUP_SECONDS=1200


def cost(start,local,remote,end):
    result=SUPPORT_RATE*Decimal(str(max(0,end-start)))/3600
    for born in (local,remote):
        if born is not None:result+=GPU_RATE*Decimal(str(max(0,end-born)))/3600
    return result


def deadlines(start,local,remote,now,original_target):
    """Treat requested node groups as fully billed; never extend the original target."""
    spent=cost(start,local,remote,now)
    rate=SUPPORT_RATE+GPU_RATE*sum(x is not None for x in (local,remote))
    left=CAP-MARGIN-spent
    if left<=0:raise ValueError('Session spending reserve exhausted')
    target=math.floor(min(original_target,now+float(left/rate*3600)))
    return {'cleanup_start_deadline_unix':target-CLEANUP_SECONDS,'deletion_target_unix':target,
            'projected_cost_usd':str(cost(start,local,remote,target)),
            'local_requested_unix':local,'remote_requested_unix':remote}


def admit_remote(start,local,now,original_target,required_work_seconds=69*60):
    result=deadlines(start,local,now,now,original_target)
    if result['cleanup_start_deadline_unix']-now < required_work_seconds:
        raise ValueError('Insufficient funded time for remote startup, minimum comparison and cleanup')
    return result

import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report
from src.services.business import analyze
from src.services.reports import deliver, reconcile
from src.services.audit import record
logging.basicConfig(level=logging.INFO)
_last_deep=0.0
def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=str(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()
def tick(now=None):
    now=time.time() if now is None else now
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    with Session() as db:
        for r in list(db.scalars(select(Report).where(Report.enabled==True,Report.next_run<=now))):
            d=deliver(db,r,now=now)
            if d:
                state='complete' if d.status=='complete' else d.status
                record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id,'period':d.period,'status':state})
                db.commit()
        # Recover interrupted archives (files written but not registered) and
        # rebuild registered ones whose files were lost or corrupted.  This is
        # idempotent, so overlapping ticks or retries cannot duplicate a period.
        global _last_deep
        deep=time.time()-_last_deep>=30
        stats=reconcile(db,deep=deep)
        if deep:_last_deep=time.time()
        if any(stats.values()):record(db,'worker','report.reconciled',stats);db.commit()
def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        db.commit()
        reconcile(db)
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop('analysis:queue',timeout=1)
            if item:execute(int(item[1]))
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)
if __name__=='__main__':main()

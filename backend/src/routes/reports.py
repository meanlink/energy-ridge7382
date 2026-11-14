import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from src.config.database import get_db
from src.config.settings import ARTIFACT_DIR
from src.models import Report, Delivery
from src.schemas.report import ReportInput
from src.middlewares.auth import current_user, admin_only
from src.services import reports
from src.services.audit import record
from src.utils.serialization import report_dict, delivery_dict
router=APIRouter()
@router.get('/reports')
def listing(db=Depends(get_db),u=Depends(current_user)):
    return [report_dict(r) for r in db.scalars(select(Report).order_by(Report.id.desc()))]
@router.post('/reports',status_code=201)
def create(data:ReportInput,db=Depends(get_db),u=Depends(admin_only)):
    r=reports.create(db,data); record(db,u.email,'report.schedule',{'id':r.id,'destination':r.destination,'job_id':r.job_id}); db.commit()
    return report_dict(r)
@router.post('/reports/{id}/toggle')
def toggle(id:int,db=Depends(get_db),u=Depends(admin_only)):
    r=db.get(Report,id)
    if not r:raise HTTPException(404,'分发计划不存在')
    r.enabled=not r.enabled;record(db,u.email,'report.toggle',{'id':id,'enabled':r.enabled});db.commit()
    return report_dict(r)
@router.get('/deliveries')
def deliveries(db=Depends(get_db),u=Depends(current_user)):
    rows=list(db.scalars(select(Delivery).order_by(Delivery.id.desc()).limit(200)))
    return [delivery_dict(d,reports.verify(d)) for d in rows]
@router.get('/deliveries/{id}')
def delivery(id:int,db=Depends(get_db),u=Depends(current_user)):
    d=db.get(Delivery,id)
    if not d:raise HTTPException(404,'归档不存在')
    return delivery_dict(d,reports.verify(d))
@router.post('/deliveries/{id}/repair')
def repair(id:int,db=Depends(get_db),u=Depends(current_user)):
    """Rebuild an archive from its frozen snapshot (any read role may trigger)."""
    d=db.get(Delivery,id)
    if not d:raise HTTPException(404,'归档不存在')
    if d.period is None or d.period < 0:raise HTTPException(409,'该归档来自旧版格式，没有可重建的期次快照，请新建分发计划')
    ok=reports.repair(db,d)
    record(db,u.email,'delivery.repair',{'id':id,'period':d.period,'ok':ok,'error':d.last_error})
    db.commit()
    if not ok:raise HTTPException(503,'归档修复失败，请稍后重试')
    return delivery_dict(d,reports.verify(d))
@router.get('/deliveries/{id}/download')
def download(id:int,format:str='csv',db=Depends(get_db),u=Depends(current_user)):
    d=db.get(Delivery,id)
    if not d:raise HTTPException(404,'归档不存在')
    if format not in ('csv','json'):raise HTTPException(422,'支持 CSV 或 JSON')
    state=reports.verify(d)
    if not state['ready']:
        raise HTTPException(409,f'{state["reason"]}，请在收件箱修复后重试')
    if (d.period is None or d.period < 0) and format=='json':
        # Legacy archives never had a verified JSON partner.
        raise HTTPException(409,'旧版归档仅有明细文件可下载')
    filename=d.filename+('.json' if format=='json' else '')
    path=os.path.join(ARTIFACT_DIR,filename)
    return FileResponse(path,filename=filename,media_type='application/json' if format=='json' else 'text/csv; charset=utf-8')

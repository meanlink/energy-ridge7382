import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','approved_at','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','period','enabled']}
def delivery_dict(d, state=None):
    state=state or {}
    return {
        'id':d.id, 'report_id':d.report_id, 'period':d.period,'due_at':d.due_at,
        'status':d.status, 'created_at':d.created_at, 'completed_at':d.completed_at,
        'checksum':d.checksum, 'filename':d.filename, 'destination':d.destination,
        'report_name':getattr(d,'report_name',''), 'job_id':d.job_id, 'job_name':getattr(d,'job_name',''),
        'params':d.params, 'csv_checksum':d.csv_checksum, 'json_checksum':d.json_checksum,
        'csv_size':d.csv_size, 'json_size':d.json_size,
        'repair_count':d.repair_count, 'repaired_at':d.repaired_at, 'last_error':d.last_error,
        'ready':state.get('ready',False), 'integrity':state.get('reason',''), 'mode':state.get('mode','snapshot'),
    }

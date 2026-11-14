"""Briefing archive delivery: one immutable snapshot per report period.

The archive lifecycle is:

    claim (DB, committed first) -> materialize two files -> verify -> mark complete

* The claim row (``Delivery`` with ``status='pending'``) is committed *before*
  any file is written and is unique on ``(report_id, period)``.  Concurrent
  workers therefore cannot create two archives for the same due slot.
* ``Delivery.params`` / ``Delivery.result`` freeze the approved analysis at the
  moment the period became due.  Every retry or repair regenerates the exact
  same bytes from that snapshot and never reads a newer ``Job.result``.
* Both files are written through a temp file + ``os.replace`` and re-read to
  confirm their sha256 before the row is marked ``complete``.  If the process
  dies after the files exist but before completion is committed, the pending
  claim is re-materialised (or, when the claim itself is lost, adopted from the
  self-describing JSON manifest).
* A completed row whose files were deleted or corrupted is rebuilt from the
  frozen snapshot; ``repair_count`` records how often this happened.
* Only a delivery whose two files are present and checksum-consistent is
  downloadable.
"""
import csv
import glob
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.config.settings import ARTIFACT_DIR
from src.models import Delivery, Job, Report

SCHEMA = 'energy-ridge/archive-v1'
ORPHAN_RE = re.compile(r'^report-(\d+)-period-(\d+)\.csv\.json$')


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def archive_name(report_id: int, period: int) -> str:
    """Deterministic, idempotent name: the same slot can only ever map to one file."""
    return f'report-{report_id}-period-{period}.csv'


def _path(filename: str) -> str:
    return os.path.join(ARTIFACT_DIR, filename)


def _safe(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


def _csv_bytes(result: dict) -> bytes:
    table = (result or {}).get('table') or {}
    columns = table.get('columns') or []
    out = io.StringIO(newline='')
    out.write('﻿')
    writer = csv.writer(out)
    writer.writerow([_safe(c['label']) for c in columns])
    for row in table.get('rows') or []:
        writer.writerow([_safe(row.get(c['key'], '')) for c in columns])
    return out.getvalue().encode('utf-8')


def _build(delivery: Delivery):
    """Deterministically render both files from the frozen snapshot."""
    csv_bytes = _csv_bytes(delivery.result)
    csv_checksum = _sha(csv_bytes)
    manifest = {
        'schema': SCHEMA,
        'report_id': delivery.report_id,
        'report_name': delivery.report_name,
        'period': delivery.period,
        'destination': delivery.destination,
        'job_id': delivery.job_id,
        'job_name': delivery.job_name,
        'approved_at': delivery.approved_at,
        'due_at': delivery.due_at,
        'created_at': delivery.created_at,
        'generated_at': delivery.created_at,
        'params': delivery.params,
        'result': delivery.result,
        'csv_filename': delivery.filename,
        'csv_checksum': csv_checksum,
    }
    json_bytes = _canonical(manifest)
    return csv_bytes, json_bytes


def _atomic_write(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.archive-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_if_changed(path: str, data: bytes) -> None:
    """Keep existing bytes when a previous attempt already produced them."""
    try:
        with open(path, 'rb') as handle:
            if handle.read() == data:
                return
    except OSError:
        pass
    _atomic_write(path, data)


def _read(path: str) -> bytes | None:
    try:
        with open(path, 'rb') as handle:
            return handle.read()
    except OSError:
        return None


def materialize(db, delivery: Delivery) -> Delivery:
    """Write (or rewrite) both archive files and stamp the verified checksums."""
    if delivery.period is None or delivery.period < 0:
        raise ValueError('旧版归档没有期次快照，无法重建')
    if not delivery.filename:
        delivery.filename = archive_name(delivery.report_id, delivery.period)
    if not delivery.snapshot_checksum:
        delivery.snapshot_checksum = _sha(_canonical({'params': delivery.params, 'result': delivery.result}))
    csv_bytes, json_bytes = _build(delivery)
    csv_checksum, json_checksum = _sha(csv_bytes), _sha(json_bytes)
    csv_path, json_path = _path(delivery.filename), _path(delivery.filename + '.json')
    _write_if_changed(csv_path, csv_bytes)
    _write_if_changed(json_path, json_bytes)
    # Verify from disk before registering completion: a torn/corrupt write must
    # stay pending so the next tick repairs it instead of advertising it.
    on_disk_csv, on_disk_json = _read(csv_path), _read(json_path)
    if on_disk_csv is None or _sha(on_disk_csv) != csv_checksum:
        raise IOError('归档明细写入后校验失败')
    if on_disk_json is None or _sha(on_disk_json) != json_checksum:
        raise IOError('归档结论写入后校验失败')
    delivery.csv_checksum = csv_checksum
    delivery.json_checksum = json_checksum
    delivery.checksum = _sha(f'{csv_checksum}:{json_checksum}'.encode())
    delivery.csv_size = len(csv_bytes)
    delivery.json_size = len(json_bytes)
    delivery.status = 'complete'
    delivery.last_error = ''
    if delivery.completed_at is None:
        delivery.completed_at = time.time()
    return delivery


def verify(delivery: Delivery) -> dict:
    """Return the inbox-facing integrity verdict for one delivery."""
    if delivery.period is None or delivery.period < 0:
        # Grandfathered archive from the original format: only the CSV checksum
        # was recorded, so verify that and let the JSON be served if present.
        data = _read(_path(delivery.filename))
        if data is None:
            return {'ready': False, 'mode': 'legacy', 'reason': '明细文件缺失'}
        if _sha(data) != delivery.checksum:
            return {'ready': False, 'mode': 'legacy', 'reason': '明细校验不一致'}
        return {'ready': True, 'mode': 'legacy', 'reason': ''}
    if delivery.status != 'complete' or not delivery.csv_checksum or not delivery.json_checksum:
        return {'ready': False, 'mode': 'snapshot', 'reason': '归档尚未完成登记'}
    csv_data = _read(_path(delivery.filename))
    json_data = _read(_path(delivery.filename + '.json'))
    if csv_data is None or json_data is None:
        return {'ready': False, 'mode': 'snapshot', 'reason': '归档文件缺失'}
    if len(csv_data) != delivery.csv_size or len(json_data) != delivery.json_size:
        return {'ready': False, 'mode': 'snapshot', 'reason': '归档文件长度不一致'}
    csv_checksum, json_checksum = _sha(csv_data), _sha(json_data)
    if csv_checksum != delivery.csv_checksum or json_checksum != delivery.json_checksum:
        return {'ready': False, 'mode': 'snapshot', 'reason': '归档文件校验不一致'}
    if _sha(f'{csv_checksum}:{json_checksum}'.encode()) != delivery.checksum:
        return {'ready': False, 'mode': 'snapshot', 'reason': '归档清单校验不一致'}
    try:
        manifest = json.loads(json_data)
    except ValueError:
        return {'ready': False, 'mode': 'snapshot', 'reason': '归档结论无法解析'}
    if (manifest.get('report_id') != delivery.report_id
            or manifest.get('period') != delivery.period
            or manifest.get('csv_checksum') != csv_checksum
            or manifest.get('csv_filename') != delivery.filename):
        return {'ready': False, 'mode': 'snapshot', 'reason': '归档明细与结论不一致'}
    return {'ready': True, 'mode': 'snapshot', 'reason': ''}


def repair(db, delivery: Delivery, actor: str = 'worker') -> bool:
    """Rebuild an archive from its frozen snapshot.

    ``repair_count`` only counts real repairs of a registered archive; finishing
    an interrupted first write (status ``pending``/``broken``) is a recovery,
    not a repair.
    """
    if delivery.period is None or delivery.period < 0:
        return False
    was_complete = delivery.status == 'complete'
    was_ready = was_complete and verify(delivery)['ready']
    try:
        materialize(db, delivery)
    except Exception as error:  # keep the row quarantined, retry next tick
        logging.exception('Archive repair failed for delivery %s', delivery.id)
        delivery.status = 'broken'
        delivery.last_error = str(error)[:990]
        return False
    if was_complete and not was_ready:
        delivery.repair_count = (delivery.repair_count or 0) + 1
        delivery.repaired_at = time.time()
    return True


def claim(db, report: Report, now: float | None = None, force: bool = False,
          expected_period: int | None = None) -> Delivery | None:
    """Atomically reserve the next due period for one report.

    Claiming is a compare-and-swap on ``reports.period``: only the writer that
    moves the counter from its observed value to the next one owns that slot.
    This works on databases without row locks (SQLite) as well as MySQL, so
    concurrent workers can never issue two archives for the same due period.
    The row is committed before any file exists, so a crash at any later point
    is recoverable.
    """
    now = time.time() if now is None else now
    db.expire_all()
    current = db.get(Report, report.id)
    if current is None or not current.enabled:
        return None
    if not force and current.next_run > now:
        return None
    observed = current.period or 0
    if expected_period is not None and observed != expected_period:
        return None
    job = db.get(Job, current.job_id)
    if job is None or job.status != 'completed' or not job.approved:
        # Nothing approved to snapshot: skip this slot instead of hot-looping.
        if not force:
            db.execute(update(Report)
                       .where(Report.id == current.id, Report.period == observed)
                       .values(next_run=now + current.interval_seconds))
            db.commit()
        return None
    period = observed + 1
    values = {'period': period}
    if not force:
        values['next_run'] = now + current.interval_seconds
    won = db.execute(
        update(Report)
        .where(Report.id == current.id, Report.period == observed)
        .values(**values)).rowcount
    if won != 1:
        # Another worker advanced the counter first: its claim owns this slot.
        db.rollback()
        return None
    delivery = Delivery(
        report_id=current.id, period=period,
        due_at=current.next_run if not force else now,
        status='pending', report_name=current.name, job_id=job.id, job_name=job.name,
        params=job.params, result=job.result,
        snapshot_checksum=_sha(_canonical({'params': job.params, 'result': job.result})),
        approved_at=job.approved_at, created_at=time.time(),
        destination=current.destination, filename=archive_name(current.id, period),
        csv_checksum='', json_checksum='', checksum='', csv_size=0, json_size=0,
        repair_count=0, last_error='',
    )
    db.add(delivery)
    try:
        db.commit()
    except IntegrityError:
        # Belt and braces: the unique (report_id, period) key rejects a double
        # claim if the counter update somehow raced.
        db.rollback()
        return None
    db.refresh(delivery)
    return delivery


def deliver(db, report: Report, now: float | None = None, force: bool = False,
            expected_period: int | None = None) -> Delivery | None:
    delivery = claim(db, report, now=now, force=force, expected_period=expected_period)
    if delivery is None:
        return None
    try:
        materialize(db, delivery)
    except Exception as error:
        logging.exception('Archive materialization failed for delivery %s', delivery.id)
        delivery.last_error = str(error)[:990]
    db.commit()
    return delivery


def adopt_orphans(db) -> int:
    """Register archives whose manifest exists on disk but whose row was lost.

    The JSON manifest is self-describing, so a crash that wrote both files
    before the claim was durable can still be recovered without mixing in a
    newer analysis result.
    """
    adopted = 0
    for path in sorted(glob.glob(os.path.join(ARTIFACT_DIR, 'report-*-period-*.csv.json'))):
        match = ORPHAN_RE.match(os.path.basename(path))
        if not match:
            continue
        report_id, period = int(match.group(1)), int(match.group(2))
        if db.scalar(select(Delivery.id).where(
                Delivery.report_id == report_id, Delivery.period == period)) is not None:
            continue
        report = db.get(Report, report_id)
        manifest_data = _read(path)
        if report is None or manifest_data is None:
            continue
        try:
            manifest = json.loads(manifest_data)
        except ValueError:
            continue
        csv_filename = archive_name(report_id, period)
        csv_data = _read(_path(csv_filename))
        if csv_data is None or _sha(csv_data) != manifest.get('csv_checksum'):
            continue
        if (manifest.get('report_id') != report_id or manifest.get('period') != period
                or manifest.get('csv_filename') != csv_filename
                or manifest.get('schema') != SCHEMA):
            continue
        delivery = Delivery(
            report_id=report_id, period=period, due_at=manifest.get('due_at'),
            status='pending', report_name=manifest.get('report_name') or report.name,
            job_id=manifest.get('job_id'), job_name=manifest.get('job_name') or '',
            params=manifest.get('params'), result=manifest.get('result'),
            snapshot_checksum=_sha(_canonical({'params': manifest.get('params'),
                                               'result': manifest.get('result')})),
            approved_at=manifest.get('approved_at'),
            created_at=manifest.get('created_at') or time.time(),
            destination=manifest.get('destination') or report.destination,
            filename=csv_filename, csv_checksum='', json_checksum='', checksum='',
            csv_size=0, json_size=0, repair_count=0, last_error='',
        )
        db.add(delivery)
        try:
            materialize(db, delivery)
            report.period = max(report.period or 0, period)
            db.commit()
        except Exception:
            db.rollback()
            continue
        adopted += 1
    return adopted


def reconcile(db, deep: bool = True) -> dict:
    """Recover pending claims and repair registered archives whose files drifted.

    ``deep`` also scans the most recent completed rows (file deletion/corruption
    after registration); the worker runs it on a throttle.
    """
    stats = {'recovered': 0, 'repaired': 0, 'failed': 0, 'adopted': 0}
    pending = list(db.scalars(select(Delivery).where(
        Delivery.period > 0, Delivery.status.in_(('pending', 'broken')))))
    for delivery in pending:
        if repair(db, delivery):
            stats['recovered'] += 1
        else:
            stats['failed'] += 1
    db.commit()
    if deep:
        # Re-check recent completed archives: deletion/corruption after
        # registration must self-heal on the next worker tick.
        recent = list(db.scalars(select(Delivery).where(
            Delivery.period > 0, Delivery.status == 'complete')
            .order_by(Delivery.id.desc()).limit(200)))
        for delivery in recent:
            if verify(delivery)['ready']:
                continue
            if repair(db, delivery):
                stats['repaired'] += 1
            else:
                stats['failed'] += 1
        db.commit()
    try:
        stats['adopted'] = adopt_orphans(db)
    except Exception:
        logging.exception('Orphan archive adoption failed')
        db.rollback()
    return stats


def create(db, data):
    job = db.get(Job, data.job_id)
    if not job or job.status != 'completed' or not job.approved:
        raise HTTPException(409, '请先选择已完成且获批准的分析结果')
    report = Report(name=data.name, job_id=job.id, destination=data.destination,
                    interval_seconds=data.interval_seconds,
                    next_run=time.time() + data.delay_seconds, period=0, enabled=True)
    db.add(report)
    db.flush()
    return report

"""Isolated collection process so Stop can interrupt a hung provider request."""
import argparse
import json
import time
from datetime import datetime, timezone
from .collectors import collect, ProviderBlocked
from .store import connect, now, upsert
from .rating import rate_jobs
from .locations import classify_location
from .collectors import PROVIDERS
from . import workflows
from . import availability


def collection_wait(path, config, run_id):
    if config.get('provider', 'linkedin') != 'linkedin' or 'job_ids' in config:
        return 0
    with connect(path) as db:
        previous = db.execute("SELECT config,finished_at FROM runs WHERE id<? AND kind='collection' AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 100", (run_id,)).fetchall()
    for row in previous:
        if json.loads(row['config']).get('provider', 'linkedin') == 'linkedin':
            stamp = datetime.fromisoformat(row['finished_at'])
            if not stamp.tzinfo:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return min(30, max(0, 30-(datetime.now(timezone.utc)-stamp).total_seconds()))
    return 0


def run(path, run_id):
    with connect(path) as db:
        row = dict(db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone())
        config = json.loads(row['config'])
        saved_ids = [r[0] for r in db.execute('SELECT job_id FROM run_jobs WHERE run_id=?', (run_id,))]
    if row['kind']=='availability':
        try:
            availability.run_checks(path,run_id)
        except Exception as error:
            with connect(path) as db:
                db.execute("UPDATE runs SET status='failed',phase='finished',error_kind='temporary',message=?,finished_at=? WHERE id=? AND status='running'",('Availability worker interrupted. Saved checks are retained. '+str(error)[:300],now(),run_id))
        return
    found, added, updated = (row[k] for k in ('found','added','updated'))
    ids = list(dict.fromkeys(list(config.get('job_ids', []))+saved_ids))
    collection_error = ''
    provider_blocked = False
    def progress(pages, scanned, note):
        with connect(path) as db:
            changed = db.execute("UPDATE runs SET pages=?,scanned=?,collection_note=?,message=?,heartbeat=? WHERE id=? AND status='running'",
                                 (pages, scanned, note, note, now(), run_id)).rowcount
            if not changed:
                raise RuntimeError('Collection stopped.')
    def save_checkpoint(value):
        with connect(path) as db:
            previous=json.loads(db.execute('SELECT checkpoint FROM runs WHERE id=?',(run_id,)).fetchone()[0])
            db.execute("UPDATE runs SET checkpoint=?,heartbeat=? WHERE id=? AND status='running'", (json.dumps(previous | value),now(),run_id))
    try:
        pause = collection_wait(path, config, run_id) if not row['collection_done'] else 0
        if pause:
            with connect(path) as db:
                db.execute("UPDATE runs SET phase='waiting',message='Spacing LinkedIn searches to reduce rate limiting…' WHERE id=? AND status='running'", (run_id,))
            time.sleep(pause)
            with connect(path) as db:
                db.execute("UPDATE runs SET phase='collecting',message='Collecting jobs; ratings will follow automatically.' WHERE id=? AND status='running'", (run_id,))
        options = {'progress':progress}
        if row['workflow_id']:
            options.update(checkpoint=json.loads(row['checkpoint']), save_checkpoint=save_checkpoint)
        for job in ([] if 'job_ids' in config or row['collection_done'] else collect(config, **options)):
            with connect(path) as db:
                state = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()[0]
                if state != 'running':
                    return
                jid, created = upsert(db, dict(job) | classify_location(job))
                availability.seen(db,jid,job)
                if jid not in ids:
                    ids.append(jid)
                    found += 1
                    added += int(created)
                    updated += int(not created)
                db.execute('INSERT OR IGNORE INTO run_jobs(run_id,job_id) VALUES(?,?)', (run_id,jid))
                workflows.remember(db,row['workflow_id'],jid,created,collected=True)
                db.execute('UPDATE runs SET found=?,added=?,updated=?,message=?,heartbeat=? WHERE id=?',
                           (found, added, updated, 'Saving '+PROVIDERS.get(config.get('provider', 'linkedin'), 'job')+' results…', now(), run_id))
        with connect(path) as db:
            db.execute("UPDATE runs SET collection_done=1 WHERE id=? AND status='running'", (run_id,))
    except Exception as error:
        collection_error = str(error)[:1000]
        provider_blocked = isinstance(error, ProviderBlocked)
        with connect(path) as db:
            checkpoint=json.loads(db.execute('SELECT checkpoint FROM runs WHERE id=?',(run_id,)).fetchone()[0])
            checkpoint.update(last_collection_error=collection_error,last_collection_error_at=now())
            db.execute("UPDATE runs SET checkpoint=? WHERE id=? AND status='running'",(json.dumps(checkpoint),run_id))
    try:
        counts = rate_jobs(path, ids, run_id, defer_reason=config.get('rating_paused', ''))
        status = 'failed' if collection_error else ('completed_with_warnings' if counts['unrated'] else 'completed')
        message = ('Collection failed: '+collection_error.rstrip('. ')+'. Saved jobs were still checked for ratings. ' if collection_error else
                   ('No matching results. Job boards may also block a search; a missing result does not establish closure. ' if not ids else ''))
        if ids:
            message += f"{counts['rated']} rated with Luna; {counts['cached']} existing ratings reused; {counts['unrated']} need attention."
        with connect(path) as db:
            note = db.execute('SELECT collection_note FROM runs WHERE id=?', (run_id,)).fetchone()[0]
            if note and not collection_error:
                message = note + ' ' + message
            rating_error = ''
            for jid in dict.fromkeys(ids):
                row = db.execute("SELECT rating_error FROM jobs WHERE id=? AND rating_status IN ('failed','blocked')", (jid,)).fetchone()
                if row:
                    rating_error = row['rating_error']
                    break
            kind = workflows.error_kind(collection_error,provider_blocked) if collection_error else workflows.error_kind(rating_error) if rating_error else ''
            db.execute("UPDATE runs SET status=?,phase='finished',message=?,rating_error=?,provider_blocked=?,error_kind=?,finished_at=? WHERE id=? AND status='running'",
                       (status, message, rating_error, int(provider_blocked), kind, now(), run_id))
    except Exception as error:
        with connect(path) as db:
            db.execute("UPDATE runs SET status='failed',error_kind=?,message=?,finished_at=? WHERE id=? AND status='running'",
                       (workflows.error_kind(str(error)), 'Saved jobs are retained. Rating worker failed: '+str(error)[:1000], now(), run_id))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--run', type=int, required=True)
    args = parser.parse_args()
    run(args.db, args.run)

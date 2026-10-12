"""Isolated collection process so Stop can interrupt a hung provider request."""
import argparse
import json
from .collectors import collect
from .store import connect, now, upsert
from .rating import rate_jobs


def run(path, run_id):
    with connect(path) as db:
        config = json.loads(db.execute('SELECT config FROM runs WHERE id=?', (run_id,)).fetchone()[0])
    found = added = updated = 0
    ids = list(config.get('job_ids', []))
    collection_error = ''
    try:
        for job in ([] if 'job_ids' in config else collect(config)):
            with connect(path) as db:
                state = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()[0]
                if state != 'running':
                    return
                jid, created = upsert(db, job)
                ids.append(jid)
                found += 1
                added += int(created)
                updated += int(not created)
                db.execute('UPDATE runs SET found=?,added=?,updated=?,message=? WHERE id=?',
                           (found, added, updated, 'Saving LinkedIn results…', run_id))
    except Exception as error:
        collection_error = str(error)[:1000]
    try:
        counts = rate_jobs(path, ids, run_id)
        status = 'failed' if collection_error else ('completed_with_warnings' if counts['unrated'] else 'completed')
        message = ('Collection failed: '+collection_error+'. Saved jobs were still checked for ratings. ' if collection_error else
                   ('No results. LinkedIn may have no matches or may be blocking the search. ' if not ids else ''))
        if ids:
            message += f"{counts['rated']} rated with Luna; {counts['cached']} existing ratings reused; {counts['unrated']} need attention."
        with connect(path) as db:
            db.execute("UPDATE runs SET status=?,phase='finished',message=?,finished_at=? WHERE id=? AND status='running'",
                       (status, message, now(), run_id))
    except Exception as error:
        with connect(path) as db:
            db.execute("UPDATE runs SET status='failed',message=?,finished_at=? WHERE id=? AND status='running'",
                       ('Saved jobs are retained. Rating worker failed: '+str(error)[:1000], now(), run_id))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--run', type=int, required=True)
    args = parser.parse_args()
    run(args.db, args.run)

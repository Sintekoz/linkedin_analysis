"""Isolated collection process so Stop can interrupt a hung provider request."""
import argparse
import json
from .collectors import collect
from .store import connect, now, upsert


def run(path, run_id):
    with connect(path) as db:
        config = json.loads(db.execute('SELECT config FROM runs WHERE id=?', (run_id,)).fetchone()[0])
    found = added = updated = 0
    try:
        for job in collect(config):
            with connect(path) as db:
                state = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()[0]
                if state != 'running':
                    return
                _, created = upsert(db, job)
                found += 1
                added += int(created)
                updated += int(not created)
                db.execute('UPDATE runs SET found=?,added=?,updated=?,message=? WHERE id=?',
                           (found, added, updated, 'Saving LinkedIn results…', run_id))
        with connect(path) as db:
            db.execute("UPDATE runs SET status='completed',message=?,finished_at=? WHERE id=? AND status='running'",
                ('Collection finished. An empty result can mean no matches or provider blocking; listings are not marked closed.' if not found else 'Collection finished.', now(), run_id))
    except Exception as error:
        with connect(path) as db:
            db.execute("UPDATE runs SET status='failed',message=?,finished_at=? WHERE id=? AND status='running'", (str(error)[:1500], now(), run_id))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--run', type=int, required=True)
    args = parser.parse_args()
    run(args.db, args.run)

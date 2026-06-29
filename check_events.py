from models.database import get_db
with get_db() as conn:
    rows = conn.execute("SELECT id, event_type, details, timestamp FROM events ORDER BY timestamp DESC LIMIT 5").fetchall()
    for r in rows:
        details = r[2][:150] if r[2] else 'NULL'
        print(f'ID={r[0]}, Type={r[1]}, Details={details}, Time={r[3]}')
    total = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='motion_start'").fetchone()[0]
    print(f'\nTotal motion_start: {total}')

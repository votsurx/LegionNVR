from models.database import get_db
with get_db() as conn:
    conn.execute('ALTER TABLE cameras ADD COLUMN ai_enabled INTEGER DEFAULT 0')
    conn.execute('ALTER TABLE cameras ADD COLUMN ai_classes TEXT DEFAULT \"[0]\"')
    conn.execute('ALTER TABLE cameras ADD COLUMN ai_confidence REAL DEFAULT 0.5')
    conn.execute('ALTER TABLE cameras ADD COLUMN ai_frame_skip INTEGER DEFAULT 5')
    conn.commit()
print('✅ Поля AI добавлены!')

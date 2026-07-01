from models.database import get_db
with get_db() as conn:
    cam = conn.execute('SELECT id, name, enabled FROM cameras WHERE id=5').fetchone()
    if cam:
        print(f'Enabled={cam[2]}')
    else:
        print('Нет камеры 5')

from models.database import get_db
with get_db() as conn:
    cam = conn.execute('SELECT id, name, ai_boxes_shift FROM cameras WHERE id=5').fetchone()
    if cam:
        print(f'ID={cam[0]}, Name={cam[1]}, Shift={cam[2]}')
    else:
        print('Камера 5 не найдена')

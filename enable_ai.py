from models.database import get_db
with get_db() as conn:
    conn.execute("UPDATE cameras SET ai_enabled=1, ai_classes='[0,2]', ai_confidence=0.4, ai_frame_skip=5 WHERE id=5")
    conn.commit()
print('✅ AI включён для камеры 5!')

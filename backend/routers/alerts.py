"""Routes: alerts (extracted from backend/main.py)."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException

import backend.main as bm

router = APIRouter()


@bm.app.get('/api/alerts/settings')
def alerts_get_settings(_: None=Depends(bm._require_admin_token)) -> dict:
    """Получить настройки алертов (токен маскирован)."""
    return {'ok': True, 'config': bm._alert_service.get_config()}


@bm.app.patch('/api/alerts/settings')
def alerts_update_settings(payload: dict, _: None=Depends(bm._require_admin_token)) -> dict:
    """Обновить настройки алертов."""
    try:
        config = bm._alert_service.update_config(payload)
        return {'ok': True, 'config': config}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@bm.app.post('/api/alerts/test')
def alerts_test(_: None=Depends(bm._require_admin_token)) -> dict:
    """Отправить тестовое сообщение в Telegram."""
    success = bm._alert_service.test_connection()
    if success:
        return {'ok': True, 'message': 'Тестовое сообщение отправлено'}
    return {'ok': False, 'message': 'Не удалось отправить. Проверьте bot_token и chat_id.'}

/**
 * Legion NVR - Frontend JavaScript
 */

// ============================================================
// ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
// ============================================================

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('show');
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }, 100);
}

// ============================================================
// КАМЕРЫ
// ============================================================

function toggleCamera(camId, enabled) {
    fetch(`/api/cameras/${camId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled ? 1 : 0 })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            // Отправляем команду на применение
            fetch(`/api/cameras/${camId}/apply`, {
                method: 'POST'
            });
            location.reload();
        } else {
            showToast('Ошибка: ' + (data.error || 'Неизвестная ошибка'), 'error');
        }
    })
    .catch(err => {
        showToast('Ошибка: ' + err, 'error');
    });
}

function toggleDetector(camId, enabled) {
    fetch(`/api/cameras/${camId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ motion_enabled: enabled ? 1 : 0 })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            fetch(`/api/cameras/${camId}/apply`, {
                method: 'POST'
            });
            location.reload();
        } else {
            showToast('Ошибка: ' + (data.error || 'Неизвестная ошибка'), 'error');
        }
    })
    .catch(err => {
        showToast('Ошибка: ' + err, 'error');
    });
}

function toggleRecord(camId, enabled) {
    fetch(`/api/cameras/${camId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ record_enabled: enabled ? 1 : 0 })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            fetch(`/api/cameras/${camId}/apply`, {
                method: 'POST'
            });
            location.reload();
        } else {
            showToast('Ошибка: ' + (data.error || 'Неизвестная ошибка'), 'error');
        }
    })
    .catch(err => {
        showToast('Ошибка: ' + err, 'error');
    });
}

// ============================================================
// ФИЛЬТР КАМЕР
// ============================================================

function filterCameras(filter, element) {
    // Убираем активный класс со всех чипсов
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    element.classList.add('active');

    // Фильтруем карточки
    document.querySelectorAll('.card[data-camera]').forEach(card => {
        if (filter === 'all') {
            card.style.display = '';
        } else if (filter.startsWith('loc_')) {
            const locId = filter.replace('loc_', '');
            card.style.display = card.dataset.location === locId ? '' : 'none';
        } else {
            card.style.display = card.dataset.camera === filter ? '' : 'none';
        }
    });
}

// ============================================================
// ЗОНЫ ДЕТЕКЦИИ (для страницы camera_zones)
// ============================================================

function loadZones(cameraId) {
    fetch(`/api/cameras/${cameraId}/zones`)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                // Обработка зон
                console.log('Зоны загружены:', data.zones);
            }
        })
        .catch(err => {
            console.error('Ошибка загрузки зон:', err);
        });
}

// ============================================================
// ФИЛЬТР ЗАПИСЕЙ
// ============================================================

function filterRecordings() {
    const cameraId = document.getElementById('filterCamera')?.value;
    const date = document.getElementById('filterDate')?.value;

    if (cameraId || date) {
        let url = '/api/recordings?';
        if (cameraId) url += `camera_id=${cameraId}&`;
        if (date) url += `date=${date}&`;
        window.location.href = url;
    }
}

// ============================================================
// ЗАПУСК ПРИ ЗАГРУЗКЕ СТРАНИЦЫ
// ============================================================

document.addEventListener('DOMContentLoaded', function() {
    // Если есть контейнер для тостов
    if (!document.querySelector('.toast-container')) {
        const container = document.createElement('div');
        container.className = 'toast-container';
        container.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 8px;
        `;
        document.body.appendChild(container);
    }

    console.log('🛡️ Legion NVR - Frontend loaded');
});
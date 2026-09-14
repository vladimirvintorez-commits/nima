# initiative — живая инициатива

Инициатива активна всегда (живой человек, не только стрим): по умолчанию ждёт
20 секунд тишины и держит 35 секунд между proactive-репликами. Старый
консервативный профиль возвращается явно: `NIMA_INITIATIVE_SILENCE=240` и
`NIMA_INITIATIVE_GAP=300`.

Источники темы: память, заранее накопленные web-факты и короткие вопросы.
Синхронный web-поиск в момент срабатывания выключен, чтобы не добавлять
многосекундную задержку (`NIMA_INITIATIVE_LIVE_WEB=1` возвращает его).

Таймеры различают:
- `user`: валидная речь/ручной ввод — основной silence timer;
- `chat`: только короткий anti-interrupt gate (12 с), поэтому активный чат не
  способен навсегда заглушить инициативу;
- обычную реплику Нимы: короткая пауза 8 с, но не полный proactive cooldown;
- proactive-реплику: отдельный gap 35 с.

Перед callback повторно проверяются новая активность, TTS, worker и ожидающие
conversation threads; guard-окно отменяет уже подготовленную тему, если человек
начал говорить. Эхо loopback отбрасывается пайплайном до обновления таймеров.

Настройки: `NIMA_INITIATIVE_STREAM_PROFILE`, `NIMA_INITIATIVE_SILENCE`,
`NIMA_INITIATIVE_GAP`, `NIMA_INITIATIVE_CHAT_QUIET`,
`NIMA_INITIATIVE_REPLY_QUIET`, `NIMA_INITIATIVE_POLL`,
`NIMA_INITIATIVE_INTERRUPT_GUARD`, `NIMA_INITIATIVE_LIVE_WEB`.

Безопасный откат: явно `NIMA_INITIATIVE_SILENCE=240` и
`NIMA_INITIATIVE_GAP=300`; полное отключение — `NIMA_INITIATIVE=0`.

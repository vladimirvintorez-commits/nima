# avatar — прозрачное Electron-окно Нимфеи

Окно без рамки и фона, поверх всех: VRM-моделька Нимы и две точки управления.

## Управление

- **Верхняя центральная точка** — перетаскивание окна по экрану.
  **Двойной щелчок по ней** — блокировка/разблокировка изменений окна
  (точки краснеют: перетаскивание, ресайз и перенос персонажа отключены).
- **Верхняя правая точка** — изменение размера. По горизонтали окно растёт
  **симметрично от центра** (точка следует за курсором), по вертикали —
  **вниз от неподвижного верхнего края** (тяни вниз — растёт, вверх —
  сжимается; иначе точка уезжала бы от курсора). Зажим 240×320 … 1200×1600.
- **Колёсико мыши над Нимфой** — приближение/отдаление камеры
  (шаг 0.15, пределы 1.0–4.0, позиция запоминается; при блокировке отключено).
- **Перетаскивание самой Нимфы** мышью по полю — двигает её внутри окна
  (позиция сохраняется в localStorage).

## Файлы

| Файл | Роль |
|------|------|
| `main.cjs` | Electron-процесс: окно, watch `avatar_state.json`, ресайз (абсолютный протокол) |
| `preload.cjs` | Мост рендерера (contextBridge) |
| `renderer/index.html` | Разметка: сцена + две точки |
| `renderer/viewer.js` | Рендерер (источник истины по визуалу): three.js + @pixiv/three-vrm |
| `renderer/viewer.bundle.js` | Собранный бандл — **пересобирать после правок viewer.js** |
| `bridge.py` | Мост из Python: файл состояния `avatar_state.json` + запуск Electron |

## Python-API (bridge.py)

```python
from avatar.bridge import AvatarBridge
avatar = AvatarBridge()
avatar.start()                                   # поднять Electron
avatar.command_avatar(mood="joy", action="speaking", outfit="Nima_maid.vrm")
avatar.set_mouth(0.6)                            # липсинк
avatar.stop()
```

Модульные функции `command_avatar / switch_outfit / launch_avatar / stop_avatar /
jump` — совместимый API для песочницы debug menu.

## Состояние (avatar_state.json)

`mood` (normal/joy/anger/…), `action` (idle/thinking/speaking/…),
`current_outfit` (имя VRM-файла из `vita_avatar_app/`), `speaking`, `mouth` (0..1).
Python пишет, Electron читает через fs.watch.

## Сборка

```
cd avatar
npm install            # один раз (electron, three, @pixiv/three-vrm, esbuild)
npm run build-vrm      # пересобрать viewer.bundle.js после правок viewer.js
```

Гардероб: все `Nima_*.vrm` лежат в `vita_avatar_app/` — путь передаётся в
Electron через `NIMA_VRM_DIR`, образ выбирается полем `current_outfit`.

## Диагностика

- Все сообщения консоли рендерера (включая ошибки) пишутся в
  `renderer_error.log` — первый файл, куда смотреть при «пустом окне».
- `NIMA_DEBUG_OPAQUE=1` — запустить окно с непрозрачным тёмным фоном:
  если Нима в этом режиме видна, а в прозрачном нет — проблема в
  композитинге прозрачности Windows, а не в рендерере.
- `NIMA_X` / `NIMA_Y` — начальная позиция окна (для тестов/настройки).

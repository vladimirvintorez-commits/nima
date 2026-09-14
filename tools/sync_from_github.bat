@echo off
rem Автосинк Нимфеи: подтягивает изменения с GitHub (каждые 5 мин по расписанию).
rem Лог: logs\github_sync.log
cd /d B:\Neyronya
echo [%date% %time%] sync start >> logs\github_sync.log
git pull --ff-only >> logs\github_sync.log 2>&1
echo [%date% %time%] sync done >> logs\github_sync.log

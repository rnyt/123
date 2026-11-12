@echo off
rem Запуск бота на Windows: двойной клик по start.bat
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Создаю окружение и ставлю зависимости ^(один раз^)...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
.venv\Scripts\python -m bot
pause

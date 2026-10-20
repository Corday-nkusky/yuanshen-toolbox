@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 需要：带 tkinter 的 Python 3.8+，并已装好依赖：
rem   pip install pyinstaller ms-toollib
rem 若默认 python 不合适，先 set PYTHON=<你的python.exe完整路径> 再运行本脚本
if not defined PYTHON set PYTHON=python

echo 正在打包 ms-pb-viewer.exe …
"%PYTHON%" -m PyInstaller ^
  --onefile --windowed --noconfirm --clean ^
  --name ms-pb-viewer ^
  --manifest "%~dp0app.manifest" ^
  --icon "%~dp0icon.ico" ^
  --add-data "%~dp0icon.ico;." ^
  --add-data "%~dp0fonts;fonts" ^
  --add-data "%~dp0cacert.pem;." ^
  --hidden-import ms_toollib ^
  --collect-submodules ms_toollib ^
  --distpath dist --workpath build --specpath build ^
  app.py

if errorlevel 1 (
  echo.
  echo 打包失败。
  pause
  exit /b 1
)
echo.
echo 完成：dist\ms-pb-viewer.exe
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Needs a Python 3.8+ that includes tkinter, plus:
rem   pip install pyinstaller ms-toollib
rem If the default python is not the right one:  set PYTHON=<full path to python.exe>
if not defined PYTHON set PYTHON=python

echo 正在生成版本资源 …
"%PYTHON%" "%~dp0make_version_info.py"
if errorlevel 1 (
  echo.
  echo 生成版本资源失败。
  pause
  exit /b 1
)

set /p APP_EXE=<"%~dp0build\app_name.txt"
set /p APP_VER=<"%~dp0build\app_ver.txt"
if not defined APP_EXE (
  echo.
  echo 读取文件名失败。
  pause
  exit /b 1
)
if not defined APP_VER (
  echo.
  echo 读取版本号失败。
  pause
  exit /b 1
)

echo 正在打包 %APP_EXE%.exe （v%APP_VER%）…
"%PYTHON%" -m PyInstaller ^
  --onefile --windowed --noconfirm --clean ^
  --name "%APP_EXE%" ^
  --version-file "%~dp0version_info.txt" ^
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

if not exist "%~dp0archive" mkdir "%~dp0archive"

rem Keep exactly one exe in dist\; older builds move to archive\ in the repo root
for %%F in ("%~dp0dist\*.exe") do (
  if /i not "%%~nxF"=="%APP_EXE%.exe" (
    echo 归档旧版到 archive\：%%~nxF
    move /y "%%~fF" "%~dp0archive\" >nul
  )
)

rem Also keep a version-named copy in archive\, so every build is traceable
copy /y "%~dp0dist\%APP_EXE%.exe" "%~dp0archive\%APP_EXE%-v%APP_VER%.exe" >nul

rem Deploy into the replay folder. A fixed file name means the shortcut never breaks.
set "DEPLOY=%APPDATA%\Metasweeper3.3.4"
if exist "%DEPLOY%" (
  echo 正在部署到 %DEPLOY% …
  rem Keep exactly one exe here: purge any exe whose name is not the fixed one
  for %%F in ("%DEPLOY%\*.exe") do (
    if /i not "%%~nxF"=="%APP_EXE%.exe" (
      echo 清走旧 exe：%%~nxF
      move /y "%%~fF" "%TEMP%\ytb-old-%RANDOM%-%%~nxF" >nul 2>&1
    )
  )
  if exist "%DEPLOY%\%APP_EXE%.exe" (
    rem A running exe cannot be overwritten, but it CAN be renamed away
    move /y "%DEPLOY%\%APP_EXE%.exe" "%TEMP%\ytb-old-%RANDOM%.exe" >nul 2>&1
    if exist "%DEPLOY%\%APP_EXE%.exe" (
      echo.
      echo 部署失败：%DEPLOY%\%APP_EXE%.exe 被占用且挪不开。
      echo 请先退出工具箱再重跑本脚本。
      pause
      exit /b 1
    )
  )
  copy /y "%~dp0dist\%APP_EXE%.exe" "%DEPLOY%\%APP_EXE%.exe" >nul
  if errorlevel 1 (
    echo.
    echo 部署失败：复制到 %DEPLOY% 出错。
    pause
    exit /b 1
  )
  rem Drop the old copies moved out above; locked ones get cleaned next round
  del /q "%TEMP%\ytb-old-*.exe" >nul 2>&1
  echo 部署完成。
) else (
  echo 跳过部署：%DEPLOY% 不存在。
)

echo.
echo 完成：dist\%APP_EXE%.exe （v%APP_VER%）

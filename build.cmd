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

set "DEPLOY=%APPDATA%\Metasweeper3.3.4"
set /a TRY=0

rem ---------------------------------------------------------------- build
rem Every build is a brand-new file, and virus scanners judge new one-file exes
rem with heuristics that are not stable: the same code can pass one build and be
rem quarantined as a trojan in the next. So every build is actually launched once;
rem if it does not start, delete it and build again (different bytes, usually passes).
rem Written in English on purpose: cmd on a UTF-8 code page misparses some CJK
rem characters in rem lines and tries to run them as commands.
:build
set /a TRY+=1
if %TRY% GTR 3 (
  echo.
  echo 连续三次打出来的 exe 都启动不了，卡在自检这一步。
  echo 把上面这段输出发给我，换一种打包方式。
  pause
  exit /b 1
)

echo 正在打包 %APP_EXE%.exe （v%APP_VER%）…第 %TRY% 次
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

call :selftest "%~dp0dist\%APP_EXE%.exe"
if errorlevel 1 (
  echo 这次打出来的 exe 启动不了，删掉重打。
  del /q "%~dp0dist\%APP_EXE%.exe" >nul 2>&1
  goto :build
)

rem ------------------------------------------------------ archive and deploy
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

  rem A new path means another scan, so verify again after deploying
  call :selftest "%DEPLOY%\%APP_EXE%.exe"
  if errorlevel 1 (
    echo 复制进录像目录后起不来（多半是刚被隔离），重打并重新部署。
    del /q "%DEPLOY%\%APP_EXE%.exe" >nul 2>&1
    del /q "%~dp0dist\%APP_EXE%.exe" >nul 2>&1
    goto :build
  )
  echo 部署完成。
) else (
  echo 跳过部署：%DEPLOY% 不存在。
)

echo.
echo 完成：dist\%APP_EXE%.exe （v%APP_VER%），已确认能启动。
exit /b 0

rem -------------------------------------------------------- launch selftest
rem Launch with --selftest: no window, no scanning, just writes a receipt and exits.
rem No receipt means the process never started: blocked, deleted, or failed to unpack.
:selftest
set "ST=%TEMP%\ytb-selftest.txt"
del /q "%ST%" >nul 2>&1
if not exist "%~1" exit /b 1
start /wait "" "%~1" --selftest
if not exist "%ST%" exit /b 1
exit /b 0

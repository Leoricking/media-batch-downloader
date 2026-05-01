@echo off
chcp 65001 > nul
cd /d %~dp0

set APP_NAME=MediaBatchDownloader
set ENTRY=main.py

echo =========================================
echo   Media Batch Downloader - Build EXE
echo =========================================
echo.

where python > nul 2>&1
if errorlevel 1 (
    echo [ERROR] 找不到 Python，請先安裝 Python 3.10+ 並加入 PATH
    pause
    exit /b 1
)

echo [1/6] 檢查 Python 版本...
python --version
if errorlevel 1 (
    echo [ERROR] Python 無法執行
    pause
    exit /b 1
)

echo.
echo [2/6] 檢查並安裝必要套件...
python -m pip install --upgrade pip
python -m pip install -U pyinstaller instaloader yt-dlp opencc-python-reimplemented tkinterdnd2 playwright requests pycryptodome keyring
if errorlevel 1 (
    echo [ERROR] 套件安裝失敗
    pause
    exit /b 1
)

echo.
echo [3/6] 確認 Playwright Chromium...
python -m playwright install chromium
if errorlevel 1 (
    echo [WARNING] Playwright Chromium 安裝可能失敗；若 Facebook / Instagram Playwright 模式不能用，請手動執行：
    echo python -m playwright install chromium
)

echo.
echo [4/6] 清理舊的 build / dist / 暫存 post...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__
if exist post rmdir /s /q post

echo.
echo [5/6] 開始打包...
python -m PyInstaller --noconfirm --onefile --noconsole ^
--name %APP_NAME% ^
--hidden-import=instaloader ^
--hidden-import=yt_dlp ^
--hidden-import=opencc ^
--hidden-import=tkinterdnd2 ^
--hidden-import=playwright ^
--hidden-import=requests ^
--collect-all playwright ^
--add-data "accounts.json;." ^
--add-data "cookies.txt;." ^
--add-data "data;data" ^
--add-data "..\pre-processing;pre-processing" ^
%ENTRY%

if errorlevel 1 (
    echo [ERROR] PyInstaller 打包失敗
    pause
    exit /b 1
)

echo.
echo [6/6] 建立發布目錄...
if exist release rmdir /s /q release
mkdir release
mkdir release\data
mkdir release\downloads

copy /y dist\%APP_NAME%.exe release\%APP_NAME%.exe > nul

if exist accounts.json copy /y accounts.json release\accounts.json > nul
if exist cookies.txt copy /y cookies.txt release\cookies.txt > nul
if exist "README.md" copy /y "README.md" release\"README.md" > nul
if exist "install.txt" copy /y "install.txt" release\"install.txt" > nul
if exist "How to use.txt" copy /y "How to use.txt" release\"How to use.txt" > nul

echo.
echo =========================================
echo [完成] EXE 路徑:
echo %cd%\release\%APP_NAME%.exe
echo.
echo 發布資料夾:
echo %cd%\release
echo =========================================
echo.
echo 之後請直接執行:
echo release\%APP_NAME%.exe
echo.

pause

@echo off
chcp 65001 > nul
cd /d %~dp0

echo =========================================
echo   Downloader - Build EXE
echo =========================================
echo.

where python > nul 2>&1
if errorlevel 1 (
    echo [ERROR] 找不到 Python，請先安裝 Python 3.10+ 並加入 PATH
    pause
    exit /b 1
)

echo [1/5] 檢查並安裝必要套件...
python -m pip install --upgrade pip
python -m pip install pyinstaller instaloader yt-dlp opencc-python-reimplemented tkinterdnd2

if errorlevel 1 (
    echo [ERROR] 套件安裝失敗
    pause
    exit /b 1
)

echo.
echo [2/5] 清理舊的 build / dist...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__

echo.
echo [3/5] 開始打包...
python -m PyInstaller --noconfirm --onefile --noconsole ^
--name Downloader ^
--hidden-import=instaloader ^
--hidden-import=yt_dlp ^
--hidden-import=opencc ^
--hidden-import=tkinterdnd2 ^
--add-data "accounts.json;." ^
--add-data "cookies.txt;." ^
--add-data "data;data" ^
--add-data "..\pre-processing;pre-processing" ^
main.py

if errorlevel 1 (
    echo [ERROR] PyInstaller 打包失敗
    pause
    exit /b 1
)

echo.
echo [4/5] 建立發布目錄...
if exist release rmdir /s /q release
mkdir release
mkdir release\data
mkdir release\downloads

copy /y dist\Downloader.exe release\Downloader.exe > nul

if exist accounts.json copy /y accounts.json release\accounts.json > nul
if exist cookies.txt copy /y cookies.txt release\cookies.txt > nul

if exist "How to use.txt" copy /y "How to use.txt" release\"How to use.txt" > nul
if exist "install.txt" copy /y "install.txt" release\"install.txt" > nul

echo.
echo [5/5] 完成
echo -----------------------------------------
echo EXE 路徑:
echo %cd%\release\Downloader.exe
echo.
echo 發布資料夾:
echo %cd%\release
echo -----------------------------------------
echo.
echo 之後請直接執行:
echo release\Downloader.exe
echo.

pause
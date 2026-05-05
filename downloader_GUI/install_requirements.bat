@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo Media Batch Downloader v11.34 Installer
echo ========================================
echo.

echo [1/4] Upgrade pip...
python -m pip install --upgrade pip
if errorlevel 1 goto error

echo.
echo [2/4] Install Python requirements...
python -m pip install -r requirements.txt
if errorlevel 1 goto error

echo.
echo [3/4] Install Playwright Chromium...
python -m playwright install chromium
if errorlevel 1 goto error

echo.
echo [4/4] Verify Playwright import...
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
if errorlevel 1 goto error

echo.
echo ========================================
echo Install completed successfully.
echo You can now run: python main.py
echo ========================================
pause
exit /b 0

:error
echo.
echo ========================================
echo Install failed. Please check the error above.
echo ========================================
pause
exit /b 1

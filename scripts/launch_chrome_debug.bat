@echo off
setlocal

set "PROFILE=%USERPROFILE%\.angelone\chrome_cdp_profile"
set "PORT=9222"
set "CHROME="

if exist "%PROGRAMFILES%\Google\Chrome\Application\chrome.exe" (
    set "CHROME=%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"
) else if exist "%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe" (
    set "CHROME=%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)

if "%CHROME%"=="" (
    echo [ERROR] Chrome not found.
    pause
    exit /b 1
)

echo [launch_chrome_debug] Chrome : %CHROME%
echo [launch_chrome_debug] Profile: %PROFILE%
echo [launch_chrome_debug] Port   : %PORT%

start "" "%CHROME%" "--remote-debugging-port=%PORT%" "--user-data-dir=%PROFILE%" "--no-first-run" "--no-default-browser-check" "--disable-extensions" "https://nxt.angelone.in/auth"

endlocal

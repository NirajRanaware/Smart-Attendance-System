@echo off
setlocal enabledelayedexpansion

:: =============================================================================
:: ESP32 Firmware Host-side End-to-End Runner Build Script
:: Stage 8.4: Live Localhost HTTP Simulation Harness
:: =============================================================================

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\..\..\"
set "MOCKS_DIR=%SCRIPT_DIR%mocks"
set "FIRMWARE_DIR=%PROJECT_ROOT%firmware\esp32_attendance_device"
set "ARDUINOJSON_DIR=%PROJECT_ROOT%tools\arduino-cli\user\libraries\ArduinoJson\src"
set "OUTPUT_EXE=%SCRIPT_DIR%firmware_e2e_runner.exe"

set "GXX="
where g++ >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "GXX=g++"
) else if exist "C:\msys64\ucrt64\bin\g++.exe" (
    set "GXX=C:\msys64\ucrt64\bin\g++.exe"
) else if exist "C:\msys64\mingw64\bin\g++.exe" (
    set "GXX=C:\msys64\mingw64\bin\g++.exe"
) else (
    echo [ERROR] G++ compiler not found!
    exit /b 1
)

echo Compiling Host-side End-to-End Firmware Integration Runner...
"%GXX%" -std=c++17 -Wall -Wextra ^
    -include "%MOCKS_DIR%\config_override.h" ^
    -I "%MOCKS_DIR%" ^
    -I "%FIRMWARE_DIR%" ^
    -I "%ARDUINOJSON_DIR%" ^
    "%SCRIPT_DIR%e2e_main.cpp" ^
    "%FIRMWARE_DIR%\buzzer.cpp" ^
    "%FIRMWARE_DIR%\rfid_scanner.cpp" ^
    "%FIRMWARE_DIR%\wifi_manager.cpp" ^
    "%FIRMWARE_DIR%\display_manager.cpp" ^
    "%FIRMWARE_DIR%\api_client.cpp" ^
    -lws2_32 ^
    -o "%OUTPUT_EXE%"

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Compilation failed with error code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo [BUILD SUCCESS] Executable created at: %OUTPUT_EXE%
exit /b 0

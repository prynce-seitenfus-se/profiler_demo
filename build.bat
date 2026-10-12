@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
pushd "%SCRIPT_DIR%" || exit /b 1
set "CLEAN_BUILD=0"
set "INSTRUMENTED=OFF"
set "BUILD_CONFIG=Debug"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="-c" goto :parse_clean
if /i "%~1"=="-i" goto :parse_instrumented
if /i "%~1"=="-h" goto :show_help
if /i "%~1"=="--help" goto :show_help
if /i "%~1"=="debug" goto :parse_debug
if /i "%~1"=="release" goto :parse_release
if /i "%~1"=="measure" goto :parse_measure
if /i "%~1"=="-m" goto :parse_measure_flag
echo [ERROR] Unknown option: %~1
goto :invalid_args

:parse_clean
set "CLEAN_BUILD=1"
shift
goto :parse_args

:parse_instrumented
set "INSTRUMENTED=ON"
shift
goto :parse_args

:parse_debug
set "BUILD_CONFIG=Debug"
shift
goto :parse_args

:parse_release
set "BUILD_CONFIG=Release"
shift
goto :parse_args

:parse_measure
set "BUILD_CONFIG=Measure"
shift
goto :parse_args

:parse_measure_flag
set "BUILD_CONFIG=Measure"
shift
goto :parse_args

:args_done
where arm-none-eabi-gcc.exe >nul 2>nul
if errorlevel 1 (
    if exist "C:\toolchains\arm\13.2.1\bin\arm-none-eabi-gcc.exe" (
        set "PATH=C:\toolchains\arm\13.2.1\bin;%PATH%"
    )
)

where ninja.exe >nul 2>nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ninja-build.Ninja_Microsoft.Winget.Source_8wekyb3d8bbwe\ninja.exe" (
        set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ninja-build.Ninja_Microsoft.Winget.Source_8wekyb3d8bbwe;%PATH%"
    ) else if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\ninja.exe" (
        set "PATH=%LOCALAPPDATA%\Microsoft\WindowsApps;%PATH%"
    )
)

where cmake.exe >nul 2>nul
if errorlevel 1 (
    echo [ERROR] CMake was not found on PATH.
    goto :build_error
)

where ninja.exe >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Ninja was not found on PATH. Install Ninja via winget: winget install Ninja-build.Ninja
    goto :build_error
)

where arm-none-eabi-gcc.exe >nul 2>nul
if errorlevel 1 (
    echo [ERROR] GNU Arm Embedded Toolchain was not found on PATH.
    goto :build_error
)

echo [CONFIGURE] %BUILD_CONFIG% build; instrumented=%INSTRUMENTED%
if "%INSTRUMENTED%"=="ON" (
    cmake --preset %BUILD_CONFIG% -DPROFILER_APP_INSTRUMENTED=ON -DPROFILER_APP_SCOPES=OFF
) else (
    cmake --preset %BUILD_CONFIG%
)
if errorlevel 1 goto :build_error

if "%CLEAN_BUILD%"=="1" (
    echo [CLEAN] Removing %BUILD_CONFIG% build outputs...
    cmake --build build\%BUILD_CONFIG% --target clean
    if errorlevel 1 goto :build_error
)

echo [BUILD] STM32G070 %BUILD_CONFIG% firmware
cmake --build --preset %BUILD_CONFIG%
if errorlevel 1 goto :build_error

echo [SUCCESS] Firmware build completed.
popd
endlocal
exit /b 0

:show_help
echo.
echo Usage: build.bat [Debug^|Release^|Measure] [-c] [-i] [-m]
echo.
echo Options:
echo   Debug          Build the Debug preset (default)
echo   Release        Build the Release preset
echo   Measure        Build the Measure preset (uninstrumented, scoped)
echo   -c             Clean the selected preset outputs before building
echo   -i             Instrument benchmark sources (-finstrument-functions)
echo   -m             Select Measure preset
echo   -h             Show this help
echo.
popd
endlocal
exit /b 0

:invalid_args
popd
endlocal
exit /b 2

:build_error
echo [FAILED] Build aborted due to errors.
popd
endlocal
exit /b 1

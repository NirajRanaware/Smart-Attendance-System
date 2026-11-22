"""
Pytest Integration Foundation for ESP32 Firmware Host Simulation.

Executes the native host-compiled C++ firmware test harness and
validates that all mock interfaces, assertion macros, and production
firmware modules pass verification.
"""

import os
import subprocess
import pytest


NATIVE_DIR = os.path.join(os.path.dirname(__file__), "native")
BUILD_BAT = os.path.join(NATIVE_DIR, "build_tests.bat")
RUNNER_EXE = os.path.join(NATIVE_DIR, "firmware_test_runner.exe")


def test_native_firmware_build_and_run():
    """
    Invokes build_tests.bat to compile and execute the complete
    host-side C++ firmware test harness.
    """
    assert os.path.exists(BUILD_BAT), f"build_tests.bat not found at {BUILD_BAT}"

    proc = subprocess.run(
        ["cmd.exe", "/c", BUILD_BAT],
        cwd=NATIVE_DIR,
        capture_output=True,
        text=True,
        timeout=120
    )

    print("\n--- STDOUT ---\n", proc.stdout)
    if proc.stderr:
        print("\n--- STDERR ---\n", proc.stderr)

    assert proc.returncode == 0, f"Host test runner build or execution failed (exit code {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
    assert "TEST RESULTS:" in proc.stdout, "Summary banner not found in test runner output"
    assert "0 failed" in proc.stdout, f"One or more native tests reported failure:\n{proc.stdout}"


def test_native_firmware_runner_executable_direct():
    """
    Directly executes the compiled firmware_test_runner.exe
    verifying fast zero-recompile test execution and exit code 0.
    """
    if not os.path.exists(RUNNER_EXE):
        # Build first if not already present
        test_native_firmware_build_and_run()

    proc = subprocess.run(
        [RUNNER_EXE],
        cwd=NATIVE_DIR,
        capture_output=True,
        text=True,
        timeout=30
    )

    assert proc.returncode == 0, f"Direct runner execution failed with exit code {proc.returncode}"
    assert "RFID_ValidationAcceptedBoundaries" in proc.stdout
    assert "API_RequestConstructionHeadersAndPayload" in proc.stdout
    assert "WiFi_ConnectBlockingSuccess" in proc.stdout
    assert "Display_ScreenSuccessIdentifiedStudent" in proc.stdout
    assert "Buzzer_PatternSuccessTiming" in proc.stdout
    assert "TEST RESULTS:" in proc.stdout
    assert "0 failed" in proc.stdout

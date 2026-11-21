/**
 * @file esp32_attendance_device.ino
 * @brief Main Firmware for ESP32 Smart Attendance System (DevKit V1).
 * 
 * Target Hardware:
 * - Microcontroller: ESP32-WROOM-32 (ESP32 DevKit V1)
 * - RFID Reader: MFRC522 (13.56 MHz SPI)
 * - Display: SSD1306 0.96" OLED (128x64 I2C)
 * - Audio: Active Buzzer (GPIO 25)
 * 
 * Functional Architecture:
 * 1. Server-Authoritative: The ESP32 acts strictly as an edge input/output device.
 *    It reads the RFID UID and transmits it to the Flask backend via POST /api/scans.
 *    All identification (Roll Number, Student Name) and business logic (session check,
 *    duplicate prevention, student active status) are resolved authoritatively by SQLite.
 * 2. Independent Readiness States: Tracks OLED, RFID reader, and Wi-Fi independently.
 *    OLED failure does not halt the MCU; headless operation via Serial continues.
 *    RFID hardware failure prevents the device from falsely showing a ready-to-scan state.
 * 3. Offline Safety: When Wi-Fi is disconnected, card scans are immediately rejected with
 *    clear feedback; no unconfirmed attendance success is ever reported.
 * 4. Privacy & Diagnostic Safety: RFID card UIDs are masked in serial logs (e.g. A1****D4).
 *    No sensitive credentials or plain-text secrets are logged.
 */

#include <Arduino.h>
#include "config.h"
#include "buzzer.h"
#include "display_manager.h"
#include "rfid_scanner.h"
#include "wifi_manager.h"
#include "api_client.h"

// Independent hardware and network readiness states
static bool displayReady = false;
static bool rfidReady = false;
static bool wifiConnected = false;

/**
 * @brief Masks an RFID UID string for diagnostic logging to protect privacy.
 * Example: "A1B2C3D4" -> "A1****D4", "1234" -> "****"
 * 
 * @param uid Full hexadecimal UID string.
 * @return String Sanitized/masked string safe for production serial logs.
 */
static String maskUid(const String& uid) {
    if (uid.length() <= 4) {
        return "****";
    }
    return uid.substring(0, 2) + "****" + uid.substring(uid.length() - 2);
}

void setup() {
    // -------------------------------------------------------------------------
    // Step 1: Initialize Serial diagnostics
    // -------------------------------------------------------------------------
    Serial.begin(SERIAL_BAUD_RATE);
    delay(500); // Allow USB serial monitor to attach
    DEBUG_PRINTLN("\n==============================================");
    DEBUG_PRINTLN("  SMART ATTENDANCE MANAGEMENT SYSTEM — ESP32  ");
    DEBUG_PRINTLN("  Device ID: " + String(DEVICE_ID));
    DEBUG_PRINTLN("  Firmware Version: " + String(FIRMWARE_VERSION));
    DEBUG_PRINTLN("==============================================");

    // -------------------------------------------------------------------------
    // Step 2: Initialize Buzzer Controller
    // -------------------------------------------------------------------------
    buzzer.begin();

    // -------------------------------------------------------------------------
    // Step 3: Initialize SSD1306 OLED Display (Non-blocking / failure-tolerant)
    // -------------------------------------------------------------------------
    displayReady = display.begin();
    if (displayReady) {
        DEBUG_PRINTLN("[INIT] SSD1306 OLED display initialized successfully.");
        display.showStartup();
        buzzer.pulse(50); // Subtle startup audio cue
        delay(1500);
    } else {
        DEBUG_PRINTLN("[WARN] SSD1306 OLED display failed to initialize. Operating in headless Serial mode.");
        buzzer.play(BUZZER_BEEP_ERROR);
        delay(500);
    }

    // -------------------------------------------------------------------------
    // Step 4: Initialize MFRC522 RFID Reader (SPI Bus)
    // -------------------------------------------------------------------------
    if (displayReady) {
        display.showInitializing("RC522 RFID SPI");
    }
    rfidReady = rfid.begin();
    if (rfidReady) {
        DEBUG_PRINTLN("[INIT] RC522 RFID reader online and responsive.");
    } else {
        DEBUG_PRINTLN("[FATAL] RC522 RFID reader initialization failed! Hardware fault or wiring issue.");
        if (displayReady) {
            display.showServerError("RC522 Hardware Fault");
        }
        buzzer.play(BUZZER_BEEP_ERROR);
        delay(1500);
    }

    // -------------------------------------------------------------------------
    // Step 5: Connect to Local 2.4 GHz Wi-Fi Network
    // -------------------------------------------------------------------------
    if (displayReady) {
        display.showWifiConnecting(wifi.getSSID());
    }
    DEBUG_PRINTLN("[INIT] Connecting to Wi-Fi SSID: " + String(wifi.getSSID()));
    wifiConnected = wifi.connectBlocking(WIFI_CONNECT_TIMEOUT);

    if (wifiConnected) {
        DEBUG_PRINTLN("[INIT] Wi-Fi connected successfully.");
        DEBUG_PRINTLN("  IP Address: " + wifi.getLocalIP());
        DEBUG_PRINTLN("  RSSI:       " + String(wifi.getRSSI()) + " dBm");
        if (displayReady) {
            display.showWifiConnected(wifi.getLocalIP().c_str(), wifi.getRSSI());
        }
        buzzer.play(BUZZER_BEEP_SUCCESS);
        delay(1500);
    } else {
        DEBUG_PRINTLN("[WARN] Initial Wi-Fi connection timed out. Background reconnection active.");
        if (displayReady) {
            display.showNetworkError();
        }
        buzzer.play(BUZZER_BEEP_NETWORK_ERROR);
        delay(1500);
    }

    // -------------------------------------------------------------------------
    // Step 6: Present Initial Operating State
    // -------------------------------------------------------------------------
    if (!rfidReady) {
        // Critical hardware fault: RFID reader unavailable. Never show ready-to-scan.
        DEBUG_PRINTLN("[SYSTEM] Device blocked: RFID hardware fault. Card scanning disabled.");
        if (displayReady) {
            display.showServerError("RC522 Hardware Fault");
        }
    } else if (!wifiConnected) {
        // RFID is operational, but network is offline.
        DEBUG_PRINTLN("[SYSTEM] Device offline: RFID scanner ready, waiting for Wi-Fi.");
        if (displayReady) {
            display.showNetworkError();
        }
    } else {
        // Fully operational: Display, RFID, and Network all ready.
        DEBUG_PRINTLN("[SYSTEM] Device is fully ready to accept RFID card taps.");
        if (displayReady) {
            display.showReady();
        }
    }
}

void loop() {
    // -------------------------------------------------------------------------
    // 1. Maintain background Wi-Fi connectivity & track status transitions
    // -------------------------------------------------------------------------
    wifi.maintainConnection();
    bool currentWifi = wifi.isConnected();

    if (wifiConnected != currentWifi) {
        wifiConnected = currentWifi;
        if (wifiConnected) {
            DEBUG_PRINTLN("[WIFI] Network reconnected. IP: " + wifi.getLocalIP());
            if (displayReady && rfidReady) {
                display.showReady();
            }
        } else {
            DEBUG_PRINTLN("[WIFI] Network connection lost.");
            if (displayReady && rfidReady) {
                display.showNetworkError();
            }
        }
    }

    // -------------------------------------------------------------------------
    // 2. Hardware guard: if RFID reader failed during boot, do not attempt scan
    // -------------------------------------------------------------------------
    if (!rfidReady) {
        delay(500); // Idle delay, scanning disabled due to hardware fault
        return;
    }

    // -------------------------------------------------------------------------
    // 3. Poll for physical RFID card tap (with hold debounce filtering)
    // -------------------------------------------------------------------------
    String cardUid;
    if (rfid.readCard(cardUid)) {
        DEBUG_PRINTLN("\n----------------------------------------------");
        DEBUG_PRINTLN("[SCAN EVENT] Card detected: " + maskUid(cardUid));

        // Immediate tactile/acoustic acknowledgment
        buzzer.play(BUZZER_BEEP_CARD_DETECTED);

        // Visual acknowledgment on OLED (if display is functional)
        if (displayReady) {
            display.showProcessing(cardUid);
        }

        // ---------------------------------------------------------------------
        // 4. Offline Guard: Reject scan immediately if Wi-Fi is unavailable
        // ---------------------------------------------------------------------
        if (!wifi.isConnected()) {
            DEBUG_PRINTLN("[OFFLINE] Cannot process scan: Wi-Fi disconnected.");
            DEBUG_PRINTLN("[OFFLINE] Attendance NOT recorded. Scan dropped.");
            if (displayReady) {
                display.showNetworkError();
            }
            buzzer.play(BUZZER_BEEP_NETWORK_ERROR);
            delay(FEEDBACK_DISPLAY_MS);
            if (displayReady) {
                display.showNetworkError();
            }
            return;
        }

        // ---------------------------------------------------------------------
        // 5. Dispatch HTTP POST request to Flask backend
        // ---------------------------------------------------------------------
        DEBUG_PRINTLN("[HTTP] Transmitting scan to backend: " + apiClient.getScanEndpointUrl());
        ScanResponse response = apiClient.sendScan(cardUid);

        // ---------------------------------------------------------------------
        // 6. Process authoritative backend decision & update interface
        // ---------------------------------------------------------------------
        switch (response.outcome) {
            case SCAN_OUTCOME_SUCCESS:
                // Attendance successfully marked Present in SQLite
                DEBUG_PRINTLN("[SUCCESS] Attendance recorded successfully!");
                DEBUG_PRINTLN("  Roll Number: " + response.rollNumber);
                DEBUG_PRINTLN("  Student:     " + response.studentName);
                DEBUG_PRINTLN("  Status:      " + response.attendanceStatus);

                if (displayReady) {
                    display.showSuccess(response.rollNumber, response.studentName, response.attendanceStatus);
                }
                buzzer.play(BUZZER_BEEP_SUCCESS);
                break;

            case SCAN_OUTCOME_DUPLICATE:
                // Student already marked Present in this active session
                DEBUG_PRINTLN("[DUPLICATE] Student already marked Present.");
                DEBUG_PRINTLN("  Roll Number: " + response.rollNumber);
                DEBUG_PRINTLN("  Student:     " + response.studentName);

                if (displayReady) {
                    display.showDuplicate(response.rollNumber, response.studentName);
                }
                buzzer.play(BUZZER_BEEP_DUPLICATE);
                break;

            case SCAN_OUTCOME_UNKNOWN_CARD:
                // UID not found in registered students table
                DEBUG_PRINTLN("[REJECTED] Unknown RFID Card UID.");
                if (displayReady) {
                    display.showUnknownCard(cardUid);
                }
                buzzer.play(BUZZER_BEEP_ERROR);
                break;

            case SCAN_OUTCOME_INACTIVE_STUDENT:
                // Student account has active = 0
                DEBUG_PRINTLN("[REJECTED] Student account is inactive.");
                if (displayReady) {
                    display.showInactiveStudent();
                }
                buzzer.play(BUZZER_BEEP_ERROR);
                break;

            case SCAN_OUTCOME_NO_SESSION:
                // No class session currently active
                DEBUG_PRINTLN("[REJECTED] No class session is active.");
                if (displayReady) {
                    display.showNoActiveSession();
                }
                buzzer.play(BUZZER_BEEP_ERROR);
                break;

            case SCAN_OUTCOME_SESSION_EXPIRED:
                // Active session has passed end_time
                DEBUG_PRINTLN("[REJECTED] Class session has ended.");
                if (displayReady) {
                    display.showSessionExpired();
                }
                buzzer.play(BUZZER_BEEP_ERROR);
                break;

            case SCAN_OUTCOME_SERVER_ERROR:
                // Backend returned HTTP 401, 400, or 500 error
                if (response.httpCode == 401) {
                    DEBUG_PRINTLN("[ERROR] Device Authentication Failed (HTTP 401). Invalid or missing API key.");
                    if (displayReady) {
                        display.showServerError("Device Auth Error");
                    }
                } else {
                    DEBUG_PRINTLN("[ERROR] Server error (HTTP " + String(response.httpCode) + "): " + response.message);
                    if (displayReady) {
                        display.showServerError(response.error.length() > 0 ? response.error : ("HTTP " + String(response.httpCode)));
                    }
                }
                buzzer.play(BUZZER_BEEP_ERROR);
                break;

            case SCAN_OUTCOME_NETWORK_ERROR:
            default:
                // Connection timed out or server unreachable
                DEBUG_PRINTLN("[ERROR] Network failure communicating with server: " + response.message);
                if (displayReady) {
                    display.showServerError("Server Offline");
                }
                buzzer.play(BUZZER_BEEP_NETWORK_ERROR);
                break;
        }

        // ---------------------------------------------------------------------
        // 7. Keep result screen visible for user inspection
        // ---------------------------------------------------------------------
        delay(FEEDBACK_DISPLAY_MS);

        // ---------------------------------------------------------------------
        // 8. Return to appropriate idle state based on network connectivity
        // ---------------------------------------------------------------------
        if (displayReady) {
            if (wifi.isConnected()) {
                display.showReady();
                DEBUG_PRINTLN("[SYSTEM] Ready for next card scan.");
            } else {
                display.showNetworkError();
                DEBUG_PRINTLN("[SYSTEM] Scan complete but network offline.");
            }
        }
    }

    // Short yield to allow ESP32 FreeRTOS background tasks (TCP stack, watchdog) to run
    delay(10);
}

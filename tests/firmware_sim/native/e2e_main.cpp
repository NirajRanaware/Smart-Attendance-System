/**
 * @file e2e_main.cpp
 * @brief Native Host-side End-to-End Firmware & Live Backend Integration Runner.
 * 
 * =============================================================================
 * SIMULATION BOUNDARIES & ARCHITECTURAL DISCLOSURE:
 * =============================================================================
 * 1. Production Modules & Functions Actually Executed:
 *    - AttendanceApiClient::sendScan(cardUid) [firmware/esp32_attendance_device/api_client.cpp]
 *    - DisplayManager::show*() [firmware/esp32_attendance_device/display_manager.cpp]
 *    - BuzzerController::play() [firmware/esp32_attendance_device/buzzer.cpp]
 * 2. Test-Only Transport & Hardware Simulation:
 *    - performLiveHttpPost() in mocks/HTTPClient.h handles real Winsock TCP sockets.
 *    - Adafruit_SSD1306 mock captures frame buffer ASCII text in software.
 *    - Arduino.h mock tracks GPIO levels and simulated millis() elapsed time.
 * 3. Excluded from Simulation:
 *    - esp32_attendance_device.ino setup() and loop() FreeRTOS orchestration.
 *    - Physical MFRC522 SPI register polling and 13.56 MHz RF tag induction.
 *    - Physical 2.4 GHz 802.11 b/g/n Wi-Fi radio association and packet transmission.
 * =============================================================================
 */

#include "Arduino.h"
#include "config_override.h"
#include "api_client.h"
#include "display_manager.h"
#include "buzzer.h"
#include "wifi_manager.h"
#include "rfid_scanner.h"

#include "mocks/Adafruit_SSD1306.h"
#include "mocks/WiFi.h"
#include "mocks/HTTPClient.h"

#include <iostream>
#include <string>
#include <vector>
#include <cstring>
#include <cstdlib>

// Storage for test-controllable credentials and server destination pointers
const char* g_mock_device_id = "ESP32_ATTENDANCE_01";
const char* g_mock_device_api_key = "test-secret-key-esp32-valid-token-32b";
const char* g_mock_server_host = "127.0.0.1";
int g_mock_server_port = 5000;

static const char* outcomeToString(ScanOutcome outcome) {
    switch (outcome) {
        case SCAN_OUTCOME_SUCCESS: return "SCAN_OUTCOME_SUCCESS";
        case SCAN_OUTCOME_DUPLICATE: return "SCAN_OUTCOME_DUPLICATE";
        case SCAN_OUTCOME_UNKNOWN_CARD: return "SCAN_OUTCOME_UNKNOWN_CARD";
        case SCAN_OUTCOME_INACTIVE_STUDENT: return "SCAN_OUTCOME_INACTIVE_STUDENT";
        case SCAN_OUTCOME_NO_SESSION: return "SCAN_OUTCOME_NO_SESSION";
        case SCAN_OUTCOME_SESSION_EXPIRED: return "SCAN_OUTCOME_SESSION_EXPIRED";
        case SCAN_OUTCOME_SERVER_ERROR: return "SCAN_OUTCOME_SERVER_ERROR";
        case SCAN_OUTCOME_NETWORK_ERROR: return "SCAN_OUTCOME_NETWORK_ERROR";
        default: return "SCAN_OUTCOME_UNKNOWN";
    }
}

static const char* outcomeToShortName(ScanOutcome outcome) {
    switch (outcome) {
        case SCAN_OUTCOME_SUCCESS: return "SUCCESS";
        case SCAN_OUTCOME_DUPLICATE: return "DUPLICATE";
        case SCAN_OUTCOME_UNKNOWN_CARD: return "UNKNOWN_CARD";
        case SCAN_OUTCOME_INACTIVE_STUDENT: return "INACTIVE_STUDENT";
        case SCAN_OUTCOME_NO_SESSION: return "NO_SESSION";
        case SCAN_OUTCOME_SESSION_EXPIRED: return "SESSION_EXPIRED";
        case SCAN_OUTCOME_SERVER_ERROR: return "SERVER_ERROR";
        case SCAN_OUTCOME_NETWORK_ERROR: return "NETWORK_ERROR";
        default: return "UNKNOWN";
    }
}

struct FeedbackResult {
    std::string displayText;
    std::string buzzerPattern;
    int buzzerDurationMs = 0;
    int buzzerTransitions = 0;
};

static FeedbackResult dispatchFirmwareFeedback(
    DisplayManager& disp,
    BuzzerController& bz,
    const ScanResponse& response,
    const String& cardUid
) {
    FeedbackResult fb;
    uint32_t startWriteCount = mockGetPinWriteCount(BUZZER_PIN);
    uint32_t startMillis = millis();

    switch (response.outcome) {
        case SCAN_OUTCOME_SUCCESS:
            disp.showSuccess(response.rollNumber, response.studentName, response.attendanceStatus);
            bz.play(BUZZER_BEEP_SUCCESS);
            fb.buzzerPattern = "BUZZER_BEEP_SUCCESS";
            break;

        case SCAN_OUTCOME_DUPLICATE:
            disp.showDuplicate(response.rollNumber, response.studentName);
            bz.play(BUZZER_BEEP_DUPLICATE);
            fb.buzzerPattern = "BUZZER_BEEP_DUPLICATE";
            break;

        case SCAN_OUTCOME_UNKNOWN_CARD:
            disp.showUnknownCard(cardUid);
            bz.play(BUZZER_BEEP_ERROR);
            fb.buzzerPattern = "BUZZER_BEEP_ERROR";
            break;

        case SCAN_OUTCOME_INACTIVE_STUDENT:
            disp.showInactiveStudent();
            bz.play(BUZZER_BEEP_ERROR);
            fb.buzzerPattern = "BUZZER_BEEP_ERROR";
            break;

        case SCAN_OUTCOME_NO_SESSION:
            disp.showNoActiveSession();
            bz.play(BUZZER_BEEP_ERROR);
            fb.buzzerPattern = "BUZZER_BEEP_ERROR";
            break;

        case SCAN_OUTCOME_SESSION_EXPIRED:
            disp.showSessionExpired();
            bz.play(BUZZER_BEEP_ERROR);
            fb.buzzerPattern = "BUZZER_BEEP_ERROR";
            break;

        case SCAN_OUTCOME_SERVER_ERROR:
            if (response.httpCode == 401) {
                disp.showServerError("Device Auth Error");
            } else {
                disp.showServerError(response.error.length() > 0 ? response.error : ("HTTP " + String(response.httpCode)));
            }
            bz.play(BUZZER_BEEP_ERROR);
            fb.buzzerPattern = "BUZZER_BEEP_ERROR";
            break;

        case SCAN_OUTCOME_NETWORK_ERROR:
        default:
            disp.showServerError("Server Offline");
            bz.play(BUZZER_BEEP_NETWORK_ERROR);
            fb.buzzerPattern = "BUZZER_BEEP_NETWORK_ERROR";
            break;
    }

    fb.buzzerDurationMs = static_cast<int>(millis() - startMillis);
    fb.buzzerTransitions = static_cast<int>(mockGetPinWriteCount(BUZZER_PIN) - startWriteCount);

    if (g_lastOledInstance) {
        fb.displayText = g_lastOledInstance->mockGetCurrentFrameText();
    }
    return fb;
}

static std::string escapeJsonString(const std::string& input) {
    std::string out;
    for (char c : input) {
        if (c == '"') out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c == '\b') out += "\\b";
        else if (c == '\f') out += "\\f";
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else out += c;
    }
    return out;
}

int main(int argc, char* argv[]) {
    std::string host = "127.0.0.1";
    int port = 5000;
    std::string cardUid = "A1B2C3D4";
    std::string deviceId = "ESP32_ATTENDANCE_01";
    std::string apiKey = "test-secret-key-esp32-valid-token-32b";
    bool wifiOffline = false;
    uint32_t timeoutMs = 5000;

    // Parse command line arguments
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--host" && i + 1 < argc) {
            host = argv[++i];
        } else if (arg == "--port" && i + 1 < argc) {
            port = std::atoi(argv[++i]);
        } else if (arg == "--uid" && i + 1 < argc) {
            cardUid = argv[++i];
        } else if (arg == "--device" && i + 1 < argc) {
            deviceId = argv[++i];
        } else if (arg == "--key" && i + 1 < argc) {
            apiKey = argv[++i];
        } else if (arg == "--offline") {
            wifiOffline = true;
        } else if (arg == "--timeout" && i + 1 < argc) {
            timeoutMs = static_cast<uint32_t>(std::atoi(argv[++i]));
        } else if (arg == "--help") {
            std::cout << "Usage: firmware_e2e_runner.exe [options]\n"
                      << "  --host <ip>        Flask server host (default: 127.0.0.1)\n"
                      << "  --port <port>      Flask server port (default: 5000)\n"
                      << "  --uid <hex>        Card UID to scan (default: A1B2C3D4)\n"
                      << "  --device <id>      Device ID header (default: ESP32_ATTENDANCE_01)\n"
                      << "  --key <token>      API Key header (default: 32-char test token)\n"
                      << "  --offline          Simulate Wi-Fi disconnected\n"
                      << "  --timeout <ms>     HTTP request timeout in ms\n";
            return 0;
        }
    }

    // Configure test environment globals
    g_mock_server_host = host.c_str();
    g_mock_server_port = port;
    g_mock_device_id = deviceId.c_str();
    g_mock_device_api_key = apiKey.c_str();

    // Enable live TCP socket transport and reset HTTP counter
    mockHttpReset();
    mockHttpSetLiveTransport(true);
    g_mockHttp.timeoutMs = timeoutMs;

    // Initialize mock peripherals and production modules
    mockResetPins();
    mockResetMillis();

    display.begin();
    buzzer.begin();

    if (wifiOffline) {
        WiFi.mockSetStatus(WL_DISCONNECTED);
    } else {
        WiFi.mockSetStatus(WL_CONNECTED);
    }

    // Initial tactile & visual tap simulation
    buzzer.play(BUZZER_BEEP_CARD_DETECTED);
    display.showProcessing(cardUid.c_str());

    std::cout << "[E2E-RUNNER] Sending scan for UID: " << cardUid
              << " to http://" << host << ":" << port << "/api/scans" << std::endl;
    std::cout << "[E2E-RUNNER] Device: " << deviceId << " | API Key: "
              << (apiKey.empty() ? "(empty)" : (apiKey.substr(0, 4) + "..." + (apiKey.length() > 4 ? apiKey.substr(apiKey.length() - 4) : "")))
              << std::endl;

    // Execute actual production C++ sendScan()
    ScanResponse resp = apiClient.sendScan(cardUid.c_str());

    // Execute actual production firmware output dispatch
    FeedbackResult fb = dispatchFirmwareFeedback(display, buzzer, resp, cardUid.c_str());

    std::cout << "[E2E-RUNNER] Outcome: " << outcomeToString(resp.outcome)
              << " | HTTP: " << resp.httpCode
              << " | scan_result: " << resp.scanResult.c_str() << std::endl;
    std::cout << "[E2E-RUNNER] Student: " << resp.studentName.c_str()
              << " | Roll: " << resp.rollNumber.c_str()
              << " | Status: " << resp.attendanceStatus.c_str() << std::endl;
    std::cout << "[E2E-RUNNER] Buzzer: " << fb.buzzerPattern
              << " (" << fb.buzzerDurationMs << " ms, " << fb.buzzerTransitions << " transitions)" << std::endl;

    // Emit standardized JSON output for pytest and automated assertion
    std::cout << "E2E_JSON_OUTPUT:{"
              << "\"outcome\":\"" << outcomeToString(resp.outcome) << "\","
              << "\"outcomeName\":\"" << outcomeToShortName(resp.outcome) << "\","
              << "\"httpCode\":" << resp.httpCode << ","
              << "\"scanResult\":\"" << escapeJsonString(resp.scanResult.c_str()) << "\","
              << "\"rollNumber\":\"" << escapeJsonString(resp.rollNumber.c_str()) << "\","
              << "\"studentName\":\"" << escapeJsonString(resp.studentName.c_str()) << "\","
              << "\"attendanceStatus\":\"" << escapeJsonString(resp.attendanceStatus.c_str()) << "\","
              << "\"message\":\"" << escapeJsonString(resp.message.c_str()) << "\","
              << "\"error\":\"" << escapeJsonString(resp.error.c_str()) << "\","
              << "\"displayText\":\"" << escapeJsonString(fb.displayText) << "\","
              << "\"buzzerPattern\":\"" << fb.buzzerPattern << "\","
              << "\"buzzerDurationMs\":" << fb.buzzerDurationMs << ","
              << "\"buzzerTransitions\":" << fb.buzzerTransitions << ","
              << "\"postCallCount\":" << mockHttpGetPostCount() << ","
              << "\"httpTransmitted\":" << (mockHttpGetPostCount() > 0 ? "true" : "false")
              << "}" << std::endl;

    return 0;
}

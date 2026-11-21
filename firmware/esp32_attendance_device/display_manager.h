/**
 * @file display_manager.h
 * @brief SSD1306 128x64 I2C OLED Display Controller for Smart Attendance Device.
 * 
 * Provides crisp visual layouts for all operating states: startup, network connection,
 * ready/idle scanner, scan processing, student identification (roll number, name, status),
 * duplicate scans, and system errors.
 */

#ifndef DISPLAY_MANAGER_H
#define DISPLAY_MANAGER_H

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"

enum DisplayState {
    DISP_STATE_STARTUP,
    DISP_STATE_WIFI_CONNECTING,
    DISP_STATE_WIFI_CONNECTED,
    DISP_STATE_READY,
    DISP_STATE_PROCESSING,
    DISP_STATE_SUCCESS,
    DISP_STATE_DUPLICATE,
    DISP_STATE_UNKNOWN_CARD,
    DISP_STATE_INACTIVE_STUDENT,
    DISP_STATE_NO_SESSION,
    DISP_STATE_SESSION_EXPIRED,
    DISP_STATE_SERVER_ERROR,
    DISP_STATE_NETWORK_ERROR
};

class DisplayManager {
public:
    DisplayManager();

    /**
     * @brief Initializes I2C bus and SSD1306 OLED panel.
     * @return true if communication succeeded, false otherwise.
     */
    bool begin();

    /**
     * @brief Clears the screen buffer and pushes blank frame to OLED.
     */
    void clear();

    /**
     * @brief Displays the firmware splash / boot screen.
     */
    void showStartup();

    /**
     * @brief Displays hardware initialization progress.
     * @param component Name of component being initialized.
     */
    void showInitializing(const char* component);

    /**
     * @brief Displays Wi-Fi connecting screen with target SSID.
     * @param ssid The Wi-Fi network name.
     */
    void showWifiConnecting(const char* ssid);

    /**
     * @brief Displays Wi-Fi connection confirmation with IP and RSSI.
     * @param ip Assigned IP address.
     * @param rssi Signal strength in dBm.
     */
    void showWifiConnected(const char* ip, int8_t rssi);

    /**
     * @brief Displays the ready/idle scanning screen.
     * @param courseTitle Optional name of the active lecture session.
     */
    void showReady(const char* courseTitle = nullptr);

    /**
     * @brief Displays card detected and processing state.
     * @param uid Hex string of the scanned RFID card.
     */
    void showProcessing(const String& uid);

    /**
     * @brief Displays successful attendance confirmation with student details.
     * @param rollNumber Genuine student roll number retrieved from SQLite.
     * @param studentName Registered student name retrieved from SQLite.
     * @param status Attendance status (e.g. "PRESENT").
     */
    void showSuccess(const String& rollNumber, const String& studentName, const String& status = "PRESENT");

    /**
     * @brief Displays duplicate attendance warning with student identity.
     * @param rollNumber Genuine student roll number.
     * @param studentName Registered student name.
     */
    void showDuplicate(const String& rollNumber, const String& studentName);

    /**
     * @brief Displays unregistered card rejection screen.
     * @param uid Scanned card UID that was not found in SQLite.
     */
    void showUnknownCard(const String& uid);

    /**
     * @brief Displays inactive student rejection screen.
     */
    void showInactiveStudent();

    /**
     * @brief Displays no active session warning.
     */
    void showNoActiveSession();

    /**
     * @brief Displays session expired warning.
     */
    void showSessionExpired();

    /**
     * @brief Displays backend server communication failure screen.
     * @param errorMsg Brief error description or HTTP status code.
     */
    void showServerError(const String& errorMsg);

    /**
     * @brief Displays Wi-Fi network disconnection screen.
     */
    void showNetworkError();

    /**
     * @brief Draws a centered text string at given Y coordinate.
     */
    void drawCenteredText(const String& text, int16_t y, uint8_t size = 1, bool invert = false);

private:
    Adafruit_SSD1306 _oled;
    bool _isInitialized;

    void drawHeader(const char* title);
    String truncateString(const String& str, size_t maxLen);
};

// Global display singleton
extern DisplayManager display;

#endif // DISPLAY_MANAGER_H

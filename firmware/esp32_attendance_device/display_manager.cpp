/**
 * @file display_manager.cpp
 * @brief Implementation of OLED Display Manager for Smart Attendance System.
 */

#include "display_manager.h"

// Global display instance
DisplayManager display;

DisplayManager::DisplayManager()
    : _oled(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET_PIN),
      _isInitialized(false) {
}

bool DisplayManager::begin() {
    // Initialize I2C communication on custom pins
    Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);

    // Initialize SSD1306 OLED (switchvcc = generate 3.3V internal charge pump)
    if (!_oled.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS)) {
        DEBUG_PRINTLN("[OLED] ERROR: SSD1306 allocation failed. Check I2C wiring!");
        return false;
    }

    _isInitialized = true;
    _oled.clearDisplay();
    _oled.setTextColor(SSD1306_WHITE);
    _oled.setTextWrap(false);
    _oled.display();

    DEBUG_PRINTLN("[OLED] Display initialized successfully.");
    return true;
}

void DisplayManager::clear() {
    if (!_isInitialized) return;
    _oled.clearDisplay();
    _oled.display();
}

String DisplayManager::truncateString(const String& str, size_t maxLen) {
    if (str.length() <= maxLen) return str;
    return str.substring(0, maxLen - 1) + "~";
}

void DisplayManager::drawHeader(const char* title) {
    _oled.fillRect(0, 0, SCREEN_WIDTH, 12, SSD1306_WHITE);
    _oled.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
    _oled.setTextSize(1);
    
    int16_t x1, y1;
    uint16_t w, h;
    _oled.getTextBounds(title, 0, 0, &x1, &y1, &w, &h);
    int16_t x = (SCREEN_WIDTH - w) / 2;
    if (x < 2) x = 2;
    _oled.setCursor(x, 2);
    _oled.print(title);

    // Reset default text color to white on black
    _oled.setTextColor(SSD1306_WHITE, SSD1306_BLACK);
}

void DisplayManager::drawCenteredText(const String& text, int16_t y, uint8_t size, bool invert) {
    _oled.setTextSize(size);
    if (invert) {
        _oled.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
    } else {
        _oled.setTextColor(SSD1306_WHITE, SSD1306_BLACK);
    }

    int16_t x1, y1;
    uint16_t w, h;
    _oled.getTextBounds(text, 0, 0, &x1, &y1, &w, &h);
    int16_t x = (SCREEN_WIDTH - w) / 2;
    if (x < 0) x = 0;
    _oled.setCursor(x, y);
    _oled.print(text);

    _oled.setTextColor(SSD1306_WHITE, SSD1306_BLACK);
}

void DisplayManager::showStartup() {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    // Decorative frame
    _oled.drawRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, SSD1306_WHITE);
    _oled.drawRect(2, 2, SCREEN_WIDTH - 4, SCREEN_HEIGHT - 4, SSD1306_WHITE);

    drawCenteredText("SMART ATTENDANCE", 12, 1);
    drawCenteredText("MANAGEMENT SYSTEM", 24, 1);
    drawCenteredText("ESP32 Device v1.0", 40, 1);

    _oled.display();
}

void DisplayManager::showInitializing(const char* component) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("SYSTEM BOOT");

    _oled.setCursor(10, 22);
    _oled.setTextSize(1);
    _oled.print("Initializing...");

    _oled.setCursor(10, 38);
    _oled.print("-> ");
    _oled.print(component);

    _oled.display();
}

void DisplayManager::showWifiConnecting(const char* ssid) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("WIFI CONNECTING");

    _oled.setTextSize(1);
    _oled.setCursor(6, 20);
    _oled.print("Network:");
    _oled.setCursor(6, 32);
    _oled.print(truncateString(String(ssid), 18));

    _oled.setCursor(6, 48);
    _oled.print("Please wait...");

    _oled.display();
}

void DisplayManager::showWifiConnected(const char* ip, int8_t rssi) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("WIFI CONNECTED");

    _oled.setTextSize(1);
    _oled.setCursor(6, 18);
    _oled.print("IP Address:");
    _oled.setCursor(6, 30);
    _oled.print(ip);

    _oled.setCursor(6, 46);
    _oled.print("Signal: ");
    _oled.print(rssi);
    _oled.print(" dBm");

    _oled.display();
}

void DisplayManager::showReady(const char* courseTitle) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("READY TO SCAN");

    if (courseTitle && strlen(courseTitle) > 0) {
        _oled.setCursor(4, 18);
        _oled.setTextSize(1);
        _oled.print(truncateString(String(courseTitle), 20));
    } else {
        _oled.setCursor(4, 18);
        _oled.setTextSize(1);
        _oled.print("Class Session Active");
    }

    _oled.drawFastHLine(4, 30, SCREEN_WIDTH - 8, SSD1306_WHITE);

    // Prompt icon & text
    drawCenteredText("TAP RFID CARD", 36, 1);
    drawCenteredText(">>> Scanner Ready <<<", 49, 1);

    _oled.display();
}

void DisplayManager::showProcessing(const String& uid) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("READING CARD");

    _oled.setTextSize(1);
    _oled.setCursor(6, 20);
    _oled.print("Card UID:");
    _oled.setCursor(6, 32);
    _oled.print(uid);

    _oled.setCursor(6, 48);
    _oled.print("Verifying server...");

    _oled.display();
}

void DisplayManager::showSuccess(const String& rollNumber, const String& studentName, const String& status) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    // Inverted banner
    drawHeader("STUDENT IDENTIFIED");

    // Line 1: Roll Number
    _oled.setCursor(4, 18);
    _oled.setTextSize(1);
    _oled.print("Roll: ");
    _oled.print(rollNumber);

    // Line 2: Student Name
    _oled.setCursor(4, 32);
    _oled.print("Name: ");
    _oled.print(truncateString(studentName, 14));

    // Divider
    _oled.drawFastHLine(4, 45, SCREEN_WIDTH - 8, SSD1306_WHITE);

    // Line 3: Status confirmation
    _oled.setCursor(4, 50);
    _oled.print("Status: ");
    _oled.print(status);

    _oled.display();
}

void DisplayManager::showDuplicate(const String& rollNumber, const String& studentName) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("DUPLICATE SCAN");

    _oled.setCursor(4, 18);
    _oled.setTextSize(1);
    _oled.print("Roll: ");
    _oled.print(rollNumber);

    _oled.setCursor(4, 32);
    _oled.print("Name: ");
    _oled.print(truncateString(studentName, 14));

    _oled.drawFastHLine(4, 45, SCREEN_WIDTH - 8, SSD1306_WHITE);

    _oled.setCursor(4, 50);
    _oled.print("ALREADY PRESENT");

    _oled.display();
}

void DisplayManager::showUnknownCard(const String& uid) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("UNKNOWN CARD");

    _oled.setTextSize(1);
    _oled.setCursor(6, 18);
    _oled.print("UID: ");
    _oled.print(truncateString(uid, 14));

    _oled.setCursor(6, 32);
    _oled.print("Card not registered!");

    _oled.drawFastHLine(4, 46, SCREEN_WIDTH - 8, SSD1306_WHITE);

    _oled.setCursor(6, 51);
    _oled.print("Please contact admin");

    _oled.display();
}

void DisplayManager::showInactiveStudent() {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("ACCOUNT INACTIVE");

    _oled.setTextSize(1);
    _oled.setCursor(6, 20);
    _oled.print("Student account");
    _oled.setCursor(6, 32);
    _oled.print("is deactivated.");

    _oled.drawFastHLine(4, 46, SCREEN_WIDTH - 8, SSD1306_WHITE);

    _oled.setCursor(6, 51);
    _oled.print("Scan rejected");

    _oled.display();
}

void DisplayManager::showNoActiveSession() {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("NO ACTIVE SESSION");

    _oled.setTextSize(1);
    _oled.setCursor(6, 20);
    _oled.print("No lecture session");
    _oled.setCursor(6, 32);
    _oled.print("is currently active.");

    _oled.drawFastHLine(4, 46, SCREEN_WIDTH - 8, SSD1306_WHITE);

    _oled.setCursor(6, 51);
    _oled.print("Activate in portal");

    _oled.display();
}

void DisplayManager::showSessionExpired() {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("SESSION ENDED");

    _oled.setTextSize(1);
    _oled.setCursor(6, 20);
    _oled.print("The active session");
    _oled.setCursor(6, 32);
    _oled.print("has expired.");

    _oled.drawFastHLine(4, 46, SCREEN_WIDTH - 8, SSD1306_WHITE);

    _oled.setCursor(6, 51);
    _oled.print("Scan rejected");

    _oled.display();
}

void DisplayManager::showServerError(const String& errorMsg) {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("SERVER ERROR");

    _oled.setTextSize(1);
    _oled.setCursor(6, 20);
    _oled.print("Cannot reach backend");

    _oled.setCursor(6, 34);
    _oled.print(truncateString(errorMsg, 20));

    _oled.drawFastHLine(4, 46, SCREEN_WIDTH - 8, SSD1306_WHITE);

    _oled.setCursor(6, 51);
    _oled.print("Check host / port");

    _oled.display();
}

void DisplayManager::showNetworkError() {
    if (!_isInitialized) return;
    _oled.clearDisplay();

    drawHeader("WIFI DISCONNECTED");

    _oled.setTextSize(1);
    _oled.setCursor(6, 20);
    _oled.print("Connection lost!");

    _oled.setCursor(6, 34);
    _oled.print("Reconnecting...");

    _oled.drawFastHLine(4, 46, SCREEN_WIDTH - 8, SSD1306_WHITE);

    _oled.setCursor(6, 51);
    _oled.print("Check router / SSID");

    _oled.display();
}

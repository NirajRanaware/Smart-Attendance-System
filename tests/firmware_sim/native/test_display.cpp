/**
 * @file test_display.cpp
 * @brief Comprehensive Unit Tests for DisplayManager (display_manager.cpp).
 * 
 * Tests:
 * 1. Display initialization success and failure.
 * 2. Headless operation safety (when initialization fails).
 * 3. All UI screens and text content verification:
 *    - Startup splash
 *    - Initializing component
 *    - Wi-Fi connecting & connected
 *    - Ready to scan (default & custom title)
 *    - Processing card
 *    - Identified student (Success)
 *    - Duplicate scan
 *    - Unknown card
 *    - Inactive student
 *    - No active session & session expired
 *    - Server error & network error
 * 4. Text truncation logic (student name 14 chars, course title 20 chars).
 * 5. Empty and long string edge cases.
 * 6. OLED clear and refresh tracking.
 */

#include "test_framework.h"
#include "Arduino.h"
#include "Wire.h"
#include "Adafruit_GFX.h"
#include "Adafruit_SSD1306.h"
#include "config.h"
#include "display_manager.h"

static Adafruit_SSD1306* s_globalDisplayOled = nullptr;

// Helper to reset OLED mock and reinitialize DisplayManager
static void setupDisplayTest(bool initSuccess = true) {
    if (!s_globalDisplayOled && g_lastOledInstance) {
        s_globalDisplayOled = g_lastOledInstance;
    }
    if (s_globalDisplayOled) {
        s_globalDisplayOled->mockReset();
        s_globalDisplayOled->mockSetBeginSuccess(initSuccess);
    }
    display.begin();
}

// =============================================================================
// PART 4.1: Initialization & Headless Operation
// =============================================================================

TEST_CASE(Display_InitSuccessAndClear) {
    setupDisplayTest(true);

    ASSERT_TRUE(s_globalDisplayOled->isInitialized);
    uint32_t prevClears = s_globalDisplayOled->clearCount;
    uint32_t prevDisplays = s_globalDisplayOled->displayCount;

    display.clear();

    ASSERT_EQ(prevClears + 1, s_globalDisplayOled->clearCount);
    ASSERT_EQ(prevDisplays + 1, s_globalDisplayOled->displayCount);
}

TEST_CASE(Display_InitFailureHeadlessSafety) {
    setupDisplayTest(true);
    uint32_t displayCountBefore = s_globalDisplayOled ? s_globalDisplayOled->displayCount : 0;
    DisplayManager headlessDisplay;

    headlessDisplay.showStartup();
    headlessDisplay.showReady("Algorithms");
    headlessDisplay.showSuccess("CS101", "Alice", "Present");
    headlessDisplay.showDuplicate("CS101", "Alice");
    headlessDisplay.showUnknownCard("1A2B3C4D");
    headlessDisplay.showServerError("500");
    headlessDisplay.clear();

    // Zero OLED frames pushed when uninitialized
    ASSERT_EQ(displayCountBefore, s_globalDisplayOled->displayCount);
}

// =============================================================================
// PART 4.2: UI Screen Contents & State Verification
// =============================================================================

TEST_CASE(Display_ScreenStartup) {
    setupDisplayTest(true);
    display.showStartup();

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("SMART ATTENDANCE") != std::string::npos);
    ASSERT_TRUE(frame.find("MANAGEMENT SYSTEM") != std::string::npos);
    ASSERT_TRUE(frame.find("ESP32 Device v1.0") != std::string::npos);
}

TEST_CASE(Display_ScreenInitializingComponent) {
    setupDisplayTest(true);
    display.showInitializing("RC522 RFID");

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("SYSTEM BOOT") != std::string::npos);
    ASSERT_TRUE(frame.find("Initializing...") != std::string::npos);
    ASSERT_TRUE(frame.find("-> RC522 RFID") != std::string::npos);
}

TEST_CASE(Display_ScreenWifiConnecting) {
    setupDisplayTest(true);
    display.showWifiConnecting("College_Campus_WiFi");

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("WIFI CONNECTING") != std::string::npos);
    ASSERT_TRUE(frame.find("Network:") != std::string::npos);
    ASSERT_TRUE(frame.find("Please wait...") != std::string::npos);
}

TEST_CASE(Display_ScreenWifiConnected) {
    setupDisplayTest(true);
    display.showWifiConnected("192.168.1.188", -58);

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("WIFI CONNECTED") != std::string::npos);
    ASSERT_TRUE(frame.find("IP Address:") != std::string::npos);
    ASSERT_TRUE(frame.find("192.168.1.188") != std::string::npos);
    ASSERT_TRUE(frame.find("Signal: -58 dBm") != std::string::npos);
}

TEST_CASE(Display_ScreenReadyDefaultAndCustom) {
    setupDisplayTest(true);

    // Default session title when null or empty
    display.showReady(nullptr);
    std::string frameDefault = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameDefault.find("READY TO SCAN") != std::string::npos);
    ASSERT_TRUE(frameDefault.find("Class Session Active") != std::string::npos);
    ASSERT_TRUE(frameDefault.find("TAP RFID CARD") != std::string::npos);

    // Custom course title
    display.showReady("CS-101 CS Fundamentals");
    std::string frameCustom = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameCustom.find("READY TO SCAN") != std::string::npos);
    // 23 chars exceeds 20 maxLen: truncated to 19 chars + "~"
    ASSERT_TRUE(frameCustom.find("CS-101 CS Fundament~") != std::string::npos);
}

TEST_CASE(Display_ScreenProcessing) {
    setupDisplayTest(true);
    display.showProcessing("1A2B3C4D");

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("READING CARD") != std::string::npos);
    ASSERT_TRUE(frame.find("Card UID:") != std::string::npos);
    ASSERT_TRUE(frame.find("1A2B3C4D") != std::string::npos);
    ASSERT_TRUE(frame.find("Verifying server...") != std::string::npos);
}

TEST_CASE(Display_ScreenSuccessIdentifiedStudent) {
    setupDisplayTest(true);
    display.showSuccess("2024-CS-001", "Alice Smith", "PRESENT");

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("STUDENT IDENTIFIED") != std::string::npos);
    ASSERT_TRUE(frame.find("Roll: 2024-CS-001") != std::string::npos);
    ASSERT_TRUE(frame.find("Name: Alice Smith") != std::string::npos);
    ASSERT_TRUE(frame.find("Status: PRESENT") != std::string::npos);
}

TEST_CASE(Display_ScreenDuplicateScan) {
    setupDisplayTest(true);
    display.showDuplicate("2024-CS-001", "Alice Smith");

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("DUPLICATE SCAN") != std::string::npos);
    ASSERT_TRUE(frame.find("Roll: 2024-CS-001") != std::string::npos);
    ASSERT_TRUE(frame.find("Name: Alice Smith") != std::string::npos);
    ASSERT_TRUE(frame.find("ALREADY PRESENT") != std::string::npos);
}

TEST_CASE(Display_ScreenUnknownCard) {
    setupDisplayTest(true);
    display.showUnknownCard("998877665544");

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("UNKNOWN CARD") != std::string::npos);
    ASSERT_TRUE(frame.find("UID: 998877665544") != std::string::npos);
    ASSERT_TRUE(frame.find("Card not registered!") != std::string::npos);
    ASSERT_TRUE(frame.find("Please contact admin") != std::string::npos);
}

TEST_CASE(Display_ScreenInactiveStudent) {
    setupDisplayTest(true);
    display.showInactiveStudent();

    std::string frame = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frame.find("ACCOUNT INACTIVE") != std::string::npos);
    ASSERT_TRUE(frame.find("Student account") != std::string::npos);
    ASSERT_TRUE(frame.find("is deactivated.") != std::string::npos);
    ASSERT_TRUE(frame.find("Scan rejected") != std::string::npos);
}

TEST_CASE(Display_ScreenNoActiveSessionAndExpired) {
    setupDisplayTest(true);

    display.showNoActiveSession();
    std::string frameNoSession = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameNoSession.find("NO ACTIVE SESSION") != std::string::npos);
    ASSERT_TRUE(frameNoSession.find("No lecture session") != std::string::npos);
    ASSERT_TRUE(frameNoSession.find("Activate in portal") != std::string::npos);

    display.showSessionExpired();
    std::string frameExpired = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameExpired.find("SESSION ENDED") != std::string::npos);
    ASSERT_TRUE(frameExpired.find("The active session") != std::string::npos);
    ASSERT_TRUE(frameExpired.find("has expired.") != std::string::npos);
}

TEST_CASE(Display_ScreenServerErrorAndNetworkError) {
    setupDisplayTest(true);

    display.showServerError("500 Internal Error");
    std::string frameServer = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameServer.find("SERVER ERROR") != std::string::npos);
    ASSERT_TRUE(frameServer.find("Cannot reach backend") != std::string::npos);
    ASSERT_TRUE(frameServer.find("500 Internal Error") != std::string::npos);
    ASSERT_TRUE(frameServer.find("Check host / port") != std::string::npos);

    display.showNetworkError();
    std::string frameNet = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameNet.find("WIFI DISCONNECTED") != std::string::npos);
    ASSERT_TRUE(frameNet.find("Connection lost!") != std::string::npos);
    ASSERT_TRUE(frameNet.find("Reconnecting...") != std::string::npos);
}

// =============================================================================
// PART 4.3: Text Truncation & String Boundary Handling
// =============================================================================

TEST_CASE(Display_StudentNameTruncationBoundary) {
    setupDisplayTest(true);

    // Exact boundary: 14 characters ("12345678901234") -> not truncated
    display.showSuccess("CS101", "12345678901234", "Present");
    std::string frameExact = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameExact.find("Name: 12345678901234") != std::string::npos);
    ASSERT_TRUE(frameExact.find("~") == std::string::npos);

    // One char above boundary: 15 characters ("123456789012345") -> truncated to 13 chars + "~"
    display.showSuccess("CS101", "123456789012345", "Present");
    std::string frameTrunc = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameTrunc.find("Name: 1234567890123~") != std::string::npos);

    // Very long name: "Wolfeschlegelsteinhausen"
    display.showDuplicate("CS101", "Wolfeschlegelsteinhausen");
    std::string frameLong = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameLong.find("Name: Wolfeschlegel~") != std::string::npos);
}

TEST_CASE(Display_EmptyAndNullStringsHandledSafely) {
    setupDisplayTest(true);

    // Empty strings should not crash or trigger out-of-bounds substrings
    display.showSuccess("", "", "");
    std::string frameSuccess = s_globalDisplayOled->mockGetCurrentFrameText();
    ASSERT_TRUE(frameSuccess.find("Roll: ") != std::string::npos);
    ASSERT_TRUE(frameSuccess.find("Name: ") != std::string::npos);

    display.showProcessing("");
    display.showUnknownCard("");
    display.showServerError("");
    ASSERT_TRUE(s_globalDisplayOled->displayCount >= 4u);
}

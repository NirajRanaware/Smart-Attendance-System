/**
 * @file test_main.cpp
 * @brief Master Test Runner & Foundation Self-Test Suite for Host Firmware Simulation.
 * 
 * Verifies:
 * 1. Assertion framework macros (pass/fail diagnostics).
 * 2. Simulated time clock and delay mechanics.
 * 3. Arduino String mock capabilities.
 * 4. GPIO mock pin simulation.
 * 5. ArduinoJson v7 serialization & parsing compatibility.
 * 
 * Production module test cases are implemented in:
 * - test_rfid.cpp
 * - test_api_client.cpp
 * - test_wifi.cpp
 * - test_display.cpp
 * - test_buzzer.cpp
 */

#include "test_framework.h"
#include "Arduino.h"
#include "config.h"
#include <ArduinoJson.h>

// Storage for test-controllable credentials pointers
const char* g_mock_device_id = "ESP32_ATTENDANCE_01";
const char* g_mock_device_api_key = "d3v1c3_s3cr3t_k3y_32_ch4r4ct3rs_!";
const char* g_mock_server_host = "192.168.1.100";
int g_mock_server_port = 5000;

// =============================================================================
// Framework & Mock Engine Self-Tests
// =============================================================================

TEST_CASE(Framework_AssertionMacrosSelfTest) {
    ASSERT_TRUE(1 == 1);
    ASSERT_TRUE(true);
    ASSERT_FALSE(1 == 2);
    ASSERT_FALSE(false);
    ASSERT_EQ(42, 42);
    ASSERT_EQ(0x92, 0x92);
    ASSERT_NE(10, 20);
    ASSERT_STR_EQ("hello", "hello");
    ASSERT_STR_NE("foo", "bar");
}

TEST_CASE(Framework_SimulatedClockAndDelay) {
    mockResetMillis();
    ASSERT_EQ(0u, millis());

    delay(100);
    ASSERT_EQ(100u, millis());

    delay(250);
    ASSERT_EQ(350u, millis());

    mockSetMillis(10000);
    ASSERT_EQ(10000u, millis());

    mockAdvanceMillis(500);
    ASSERT_EQ(10500u, millis());
}

TEST_CASE(Framework_ArduinoStringOperations) {
    String emptyStr;
    ASSERT_EQ(0u, emptyStr.length());
    ASSERT_TRUE(emptyStr.isEmpty());

    String s1 = "SmartAttendance";
    ASSERT_EQ(15u, s1.length());
    ASSERT_STR_EQ("SmartAttendance", s1.c_str());

    String sFromInt(12345);
    ASSERT_STR_EQ("12345", sFromInt.c_str());

    String sFromHex(255, 16);
    ASSERT_STR_EQ("FF", sFromHex.c_str());

    String sConcat = s1 + "_" + "ESP32";
    ASSERT_STR_EQ("SmartAttendance_ESP32", sConcat.c_str());

    String sub = s1.substring(0, 5);
    ASSERT_STR_EQ("Smart", sub.c_str());

    ASSERT_EQ(5, s1.indexOf("Attendance"));
    ASSERT_EQ(-1, s1.indexOf("NotFound"));
    ASSERT_TRUE(s1.startsWith("Smart"));
    ASSERT_TRUE(s1.endsWith("Attendance"));

    String sUpper = s1;
    sUpper.toUpperCase();
    ASSERT_STR_EQ("SMARTATTENDANCE", sUpper.c_str());

    String sLower = s1;
    sLower.toLowerCase();
    ASSERT_STR_EQ("smartattendance", sLower.c_str());

    ASSERT_TRUE(sUpper.equalsIgnoreCase(sLower));
    ASSERT_FALSE(sUpper.equals(sLower));

    String sSpaces = "   padded string \t\r\n ";
    sSpaces.trim();
    ASSERT_STR_EQ("padded string", sSpaces.c_str());

    String sNum = "4200";
    ASSERT_EQ(4200, sNum.toInt());
}

TEST_CASE(Framework_GPIOPinStateTracking) {
    mockResetPins();

    pinMode(BUZZER_PIN, OUTPUT);
    ASSERT_EQ(OUTPUT, mockGetPinMode(BUZZER_PIN));

    digitalWrite(BUZZER_PIN, HIGH);
    ASSERT_EQ(HIGH, digitalRead(BUZZER_PIN));
    ASSERT_EQ(HIGH, mockGetPinState(BUZZER_PIN));
    ASSERT_EQ(1u, mockGetPinWriteCount(BUZZER_PIN));

    digitalWrite(BUZZER_PIN, LOW);
    ASSERT_EQ(LOW, digitalRead(BUZZER_PIN));
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
    ASSERT_EQ(2u, mockGetPinWriteCount(BUZZER_PIN));
}

TEST_CASE(Framework_ArduinoJsonCompatibility) {
    JsonDocument doc;
    doc["card_uid"] = "1A2B3C4D";
    doc["source"] = "ESP32_ATTENDANCE_01";
    doc["timestamp"] = 1699000000;

    String jsonString;
    serializeJson(doc, jsonString);

    ASSERT_TRUE(jsonString.indexOf("1A2B3C4D") >= 0);
    ASSERT_TRUE(jsonString.indexOf("ESP32_ATTENDANCE_01") >= 0);

    JsonDocument parseDoc;
    DeserializationError err = deserializeJson(parseDoc, jsonString.c_str());
    ASSERT_FALSE(err);
    ASSERT_TRUE(parseDoc["card_uid"].is<const char*>());
    ASSERT_STR_EQ("1A2B3C4D", parseDoc["card_uid"].as<String>().c_str());
    ASSERT_STR_EQ("ESP32_ATTENDANCE_01", parseDoc["source"].as<String>().c_str());
}

// =============================================================================
// Master Main Entry Point
// =============================================================================
int main() {
    return TestFramework::runAllTests();
}

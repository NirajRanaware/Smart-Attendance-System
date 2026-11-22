/**
 * @file test_wifi.cpp
 * @brief Comprehensive Unit Tests for WiFiManager (wifi_manager.cpp).
 * 
 * Tests:
 * 1. Initial disconnected state.
 * 2. Successful blocking connection.
 * 3. Connection timeout handling.
 * 4. Maintain connection: drop detection & retry pacing (10,000ms).
 * 5. Exact reconnection threshold & ±1ms boundary.
 * 6. Connection recovery state transitions.
 * 7. Local IP and RSSI retrieval.
 * 8. Millis rollover handling in reconnection pacing.
 */

#include "test_framework.h"
#include "Arduino.h"
#include "WiFi.h"
#include "config.h"
#include "wifi_manager.h"

// Helper to reset Wi-Fi manager and mock state
static void setupWifiTest() {
    mockResetMillis();
    WiFi.mockReset();
}

// =============================================================================
// PART 3.1: Connection Lifecycle & States
// =============================================================================

TEST_CASE(WiFi_InitialDisconnectedState) {
    setupWifiTest();
    ASSERT_FALSE(wifi.isConnected());
}

TEST_CASE(WiFi_ConnectBlockingSuccess) {
    setupWifiTest();
    WiFi.mockSetAutoConnect(true);

    bool connected = wifi.connectBlocking(5000);

    ASSERT_TRUE(connected);
    ASSERT_TRUE(wifi.isConnected());
    ASSERT_EQ(WIFI_STA, WiFi.currentMode);
    ASSERT_STR_EQ(DEVICE_ID, WiFi.hostname.c_str());
    ASSERT_STR_EQ(WIFI_SSID, WiFi.currentSSID.c_str());
}

TEST_CASE(WiFi_ConnectBlockingTimeout) {
    setupWifiTest();
    // Simulate Wi-Fi AP unreachable / bad credentials
    WiFi.mockSetAutoConnect(false);
    WiFi.mockSetStatus(WL_DISCONNECTED);

    mockSetMillis(0);
    bool connected = wifi.connectBlocking(1000); // 1000ms timeout

    ASSERT_FALSE(connected);
    ASSERT_FALSE(wifi.isConnected());
    // Time must have advanced by at least the timeout duration
    ASSERT_TRUE(millis() >= 1000u);
}

TEST_CASE(WiFi_LocalIPAndRSSIRetrieval) {
    setupWifiTest();
    WiFi.mockSetLocalIP("10.0.0.42");
    WiFi.mockSetRSSI(-54);

    ASSERT_STR_EQ("10.0.0.42", wifi.getLocalIP().c_str());
    ASSERT_EQ(-54, wifi.getRSSI());
    ASSERT_STR_EQ(WIFI_SSID, wifi.getSSID());
}

// =============================================================================
// PART 3.2: Reconnection Pacing & Thresholds
// =============================================================================

TEST_CASE(WiFi_MaintainConnectionDropAndPacing) {
    setupWifiTest();
    WiFi.mockSetAutoConnect(true);

    // 1. Establish initial connection at t=0
    mockSetMillis(0);
    ASSERT_TRUE(wifi.connectBlocking(2000));
    ASSERT_TRUE(wifi.isConnected());

    // 2. Wi-Fi drops at t=2000ms
    mockSetMillis(2000);
    WiFi.mockSetStatus(WL_DISCONNECTED);
    WiFi.mockSetAutoConnect(false); // Disallow auto-reconnect on begin

    int prevReconnectCount = WiFi.reconnectCallCount;

    // First maintenance cycle detects drop and resets timer
    wifi.maintainConnection();
    ASSERT_FALSE(wifi.isConnected());
    ASSERT_EQ(prevReconnectCount, WiFi.reconnectCallCount);

    // 3. Pacing: Check at t=5000ms (elapsed 3000ms < 10000ms retry interval)
    mockSetMillis(5000);
    wifi.maintainConnection();
    ASSERT_EQ(prevReconnectCount, WiFi.reconnectCallCount); // Still suppressed

    // 4. Exactly 1ms below threshold: t=11999ms (elapsed 9999ms < 10000ms)
    mockSetMillis(11999);
    wifi.maintainConnection();
    ASSERT_EQ(prevReconnectCount, WiFi.reconnectCallCount); // Still suppressed

    // 5. Exact threshold: t=12000ms (elapsed 10000ms == WIFI_RETRY_INTERVAL)
    mockSetMillis(12000);
    wifi.maintainConnection();
    ASSERT_EQ(prevReconnectCount + 1, WiFi.reconnectCallCount); // Reconnect triggered!

    // 6. Next attempt before interval elapses: t=15000ms (elapsed 3000ms < 10000ms)
    mockSetMillis(15000);
    wifi.maintainConnection();
    ASSERT_EQ(prevReconnectCount + 1, WiFi.reconnectCallCount); // No new attempt

    // 7. Exactly 1ms above next threshold: t=22001ms (elapsed 10001ms > 10000ms)
    mockSetMillis(22001);
    wifi.maintainConnection();
    ASSERT_EQ(prevReconnectCount + 2, WiFi.reconnectCallCount); // Second attempt triggered!
}

TEST_CASE(WiFi_MaintainConnectionRecovery) {
    setupWifiTest();
    WiFi.mockSetAutoConnect(true);
    wifi.connectBlocking(2000);

    // Drop connection
    WiFi.mockSetStatus(WL_DISCONNECTED);
    wifi.maintainConnection();
    ASSERT_FALSE(wifi.isConnected());

    // Connection restored by external event or router reboot
    WiFi.mockSetStatus(WL_CONNECTED);
    wifi.maintainConnection();
    ASSERT_TRUE(wifi.isConnected());
}

TEST_CASE(WiFi_MaintainConnectionMillisRollover) {
    setupWifiTest();
    WiFi.mockSetAutoConnect(true);

    // Initial connection right before rollover
    mockSetMillis(0xFFFFFF00);
    wifi.connectBlocking(2000);

    // Connection drops at 0xFFFFFF80
    mockSetMillis(0xFFFFFF80);
    WiFi.mockSetStatus(WL_DISCONNECTED);
    WiFi.mockSetAutoConnect(false);
    wifi.maintainConnection();

    int startReconnects = WiFi.reconnectCallCount;

    // Time rolls over past 0 to 0x00000100 (elapsed: 256 + 128 = 384ms < 10000ms)
    mockSetMillis(0x00000100);
    wifi.maintainConnection();
    ASSERT_EQ(startReconnects, WiFi.reconnectCallCount); // Suppressed

    // Advance post-rollover to elapsed >= 10000ms:
    // 0xFFFFFF80 + 10000 = 0x000026F0
    mockSetMillis(0x000026F0);
    wifi.maintainConnection();
    ASSERT_EQ(startReconnects + 1, WiFi.reconnectCallCount); // Unsigned math correctly triggers reconnect!
}

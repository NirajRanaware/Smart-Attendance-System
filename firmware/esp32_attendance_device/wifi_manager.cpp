/**
 * @file wifi_manager.cpp
 * @brief Implementation of Wi-Fi Connection Manager for ESP32.
 */

#include "wifi_manager.h"

// Global Wi-Fi instance
WiFiManager wifi;

WiFiManager::WiFiManager()
    : _lastReconnectAttempt(0), _wasConnected(false) {
}

bool WiFiManager::connectBlocking(uint32_t timeoutMs) {
    // Set station mode and disconnect any previous connection
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(true);
    delay(100);

    // Set a friendly network hostname
    WiFi.setHostname(DEVICE_ID);

    DEBUG_PRINTLN("[WIFI] Connecting to SSID: " + String(WIFI_SSID));
    // Note: Never log password to preserve network security
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - start) < timeoutMs) {
        delay(300);
        DEBUG_PRINT(".");
    }
    DEBUG_PRINTLN("");

    if (WiFi.status() == WL_CONNECTED) {
        _wasConnected = true;
        DEBUG_PRINTLN("[WIFI] Connected successfully!");
        DEBUG_PRINTLN("[WIFI] IP Address: " + WiFi.localIP().toString());
        DEBUG_PRINTLN("[WIFI] Signal: " + String(WiFi.RSSI()) + " dBm");
        return true;
    } else {
        _wasConnected = false;
        DEBUG_PRINTLN("[WIFI] ERROR: Connection timed out.");
        return false;
    }
}

void WiFiManager::maintainConnection() {
    bool currentConnected = (WiFi.status() == WL_CONNECTED);

    // Detect disconnection event
    if (_wasConnected && !currentConnected) {
        DEBUG_PRINTLN("[WIFI] WARNING: Connection lost. Will attempt reconnection...");
        _wasConnected = false;
        _lastReconnectAttempt = millis();
    }

    // Auto-reconnect with interval pacing (non-blocking)
    if (!currentConnected) {
        unsigned long now = millis();
        if (now - _lastReconnectAttempt >= WIFI_RETRY_INTERVAL) {
            _lastReconnectAttempt = now;
            DEBUG_PRINTLN("[WIFI] Attempting auto-reconnection to " + String(WIFI_SSID) + "...");
            WiFi.reconnect();
        }
    } else {
        _wasConnected = true;
    }
}

bool WiFiManager::isConnected() {
    return (WiFi.status() == WL_CONNECTED);
}

String WiFiManager::getLocalIP() {
    return WiFi.localIP().toString();
}

int8_t WiFiManager::getRSSI() {
    return WiFi.RSSI();
}

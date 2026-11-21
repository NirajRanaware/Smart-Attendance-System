/**
 * @file wifi_manager.h
 * @brief Wi-Fi Connection Manager for ESP32 Smart Attendance System.
 * 
 * Handles network connection, non-blocking auto-reconnection, signal strength
 * monitoring, and network status reporting.
 */

#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <Arduino.h>
#include <WiFi.h>
#include "config.h"

class WiFiManager {
public:
    WiFiManager();

    /**
     * @brief Configures station mode and attempts initial Wi-Fi connection.
     * @param timeoutMs Maximum time to wait for initial connection in milliseconds.
     * @return true if connected, false if timed out.
     */
    bool connectBlocking(uint32_t timeoutMs = WIFI_CONNECT_TIMEOUT);

    /**
     * @brief Checks connection status and triggers non-blocking reconnection if disconnected.
     * Should be called periodically in the main loop.
     */
    void maintainConnection();

    /**
     * @brief Returns true if Wi-Fi is currently connected.
     */
    bool isConnected();

    /**
     * @brief Returns the device's assigned local IP address string.
     */
    String getLocalIP();

    /**
     * @brief Returns current RSSI signal strength in dBm.
     */
    int8_t getRSSI();

    /**
     * @brief Returns the configured SSID name.
     */
    const char* getSSID() const { return WIFI_SSID; }

private:
    unsigned long _lastReconnectAttempt;
    bool _wasConnected;
};

// Global Wi-Fi manager singleton
extern WiFiManager wifi;

#endif // WIFI_MANAGER_H

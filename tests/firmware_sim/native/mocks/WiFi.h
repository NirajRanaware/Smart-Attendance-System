/**
 * @file WiFi.h
 * @brief Host-side Mock for ESP32 WiFi Library.
 */

#ifndef WIFI_H
#define WIFI_H

#include "Arduino.h"

enum wifi_mode_t {
    WIFI_OFF = 0,
    WIFI_STA = 1,
    WIFI_AP = 2,
    WIFI_AP_STA = 3
};

enum wl_status_t {
    WL_NO_SHIELD        = 255,
    WL_IDLE_STATUS      = 0,
    WL_NO_SSID_AVAIL    = 1,
    WL_SCAN_COMPLETED   = 2,
    WL_CONNECTED        = 3,
    WL_CONNECT_FAILED   = 4,
    WL_CONNECTION_LOST  = 5,
    WL_DISCONNECTED     = 6
};

class IPAddress {
private:
    std::string _ip;

public:
    IPAddress() : _ip("0.0.0.0") {}
    IPAddress(const char* ip) : _ip(ip ? ip : "0.0.0.0") {}
    IPAddress(uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
        char buf[20];
        snprintf(buf, sizeof(buf), "%u.%u.%u.%u", a, b, c, d);
        _ip = buf;
    }

    String toString() const {
        return String(_ip.c_str());
    }
};

class WiFiClass {
public:
    wifi_mode_t currentMode = WIFI_OFF;
    wl_status_t currentStatus = WL_DISCONNECTED;
    std::string currentSSID = "";
    std::string currentPassword = "";
    std::string hostname = "";
    IPAddress assignedIP = IPAddress("192.168.1.150");
    int8_t signalStrength = -65;
    bool autoConnectOnBegin = true;
    int reconnectCallCount = 0;
    int disconnectCallCount = 0;

    void mode(wifi_mode_t m) {
        currentMode = m;
    }

    bool disconnect(bool wifioff = false) {
        currentStatus = WL_DISCONNECTED;
        disconnectCallCount++;
        if (wifioff) currentMode = WIFI_OFF;
        return true;
    }

    void setHostname(const char* name) {
        hostname = name ? name : "";
    }

    wl_status_t begin(const char* ssid, const char* passphrase = nullptr) {
        currentMode = WIFI_STA;
        currentSSID = ssid ? ssid : "";
        currentPassword = passphrase ? passphrase : "";
        if (autoConnectOnBegin) {
            currentStatus = WL_CONNECTED;
        }
        return currentStatus;
    }

    wl_status_t status() {
        return currentStatus;
    }

    IPAddress localIP() {
        return assignedIP;
    }

    int8_t RSSI() {
        return signalStrength;
    }

    bool reconnect() {
        reconnectCallCount++;
        if (autoConnectOnBegin) {
            currentStatus = WL_CONNECTED;
            return true;
        }
        return false;
    }

    // =========================================================================
    // Mock Controls
    // =========================================================================
    void mockSetStatus(wl_status_t st) { currentStatus = st; }
    void mockSetLocalIP(const char* ip) { assignedIP = IPAddress(ip); }
    void mockSetRSSI(int8_t rssi) { signalStrength = rssi; }
    void mockSetAutoConnect(bool autoConnect) { autoConnectOnBegin = autoConnect; }
    
    void mockReset() {
        currentMode = WIFI_OFF;
        currentStatus = WL_DISCONNECTED;
        currentSSID = "";
        currentPassword = "";
        hostname = "";
        assignedIP = IPAddress("192.168.1.150");
        signalStrength = -65;
        autoConnectOnBegin = true;
        reconnectCallCount = 0;
        disconnectCallCount = 0;
    }
};

inline WiFiClass WiFi;

#endif // WIFI_H

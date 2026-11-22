/**
 * @file WiFiClient.h
 * @brief Host-side Mock for ESP32 WiFiClient.
 */

#ifndef WIFI_CLIENT_H
#define WIFI_CLIENT_H

#include "Arduino.h"
#include "WiFi.h"

class WiFiClient {
public:
    bool connected() { return true; }
    void stop() {}
};

#endif // WIFI_CLIENT_H

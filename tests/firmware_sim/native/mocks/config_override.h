/**
 * @file config_override.h
 * @brief Pre-included Configuration Shim for Native Host Testing.
 * 
 * Injected via GCC -include flag before compiling test and production units.
 * Loads the real production config.h, then binds DEVICE_ID, DEVICE_API_KEY,
 * SERVER_HOST, and SERVER_PORT to controllable pointers/variables for
 * dynamic authentication and localhost E2E integration testing.
 */

#ifndef CONFIG_OVERRIDE_H
#define CONFIG_OVERRIDE_H

// Include the real production configuration
#include "config.h"

// Override credentials & server macros with test-controllable variables
#undef DEVICE_ID
#undef DEVICE_API_KEY
#undef SERVER_HOST
#undef SERVER_PORT

extern const char* g_mock_device_id;
extern const char* g_mock_device_api_key;
extern const char* g_mock_server_host;
extern int g_mock_server_port;

#define DEVICE_ID g_mock_device_id
#define DEVICE_API_KEY g_mock_device_api_key
#define SERVER_HOST g_mock_server_host
#define SERVER_PORT g_mock_server_port

inline void mockResetDeviceCredentials() {
    g_mock_device_id = "ESP32_ATTENDANCE_01";
    g_mock_device_api_key = "d3v1c3_s3cr3t_k3y_32_ch4r4ct3rs_!";
    g_mock_server_host = "192.168.1.100";
    g_mock_server_port = 5000;
}

#endif // CONFIG_OVERRIDE_H

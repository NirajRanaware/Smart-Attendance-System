/**
 * @file config.example.h
 * @brief Hardware and Network Configuration Template for ESP32 Smart Attendance Device.
 * 
 * Central configuration template containing pin mappings, network settings,
 * server endpoints, timing constants, and operational parameters.
 * 
 * INSTRUCTIONS:
 * 1. Copy this file to 'config.h':
 *      Copy-Item config.example.h config.h
 * 2. Replace the placeholders below with your local Wi-Fi SSID, password,
 *    Flask server LAN IP address, and Device API Key.
 * 3. Never commit or upload 'config.h' to version control.
 */

#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

// =============================================================================
// 1. DEVICE IDENTIFICATION
// =============================================================================
#define DEVICE_ID             "ESP32_ATTENDANCE_01"  ///< Hardware device identifier sent to backend
#define FIRMWARE_VERSION      "1.0.0"                ///< Semantic firmware version

// =============================================================================
// 2. WI-FI NETWORK CONFIGURATION
// =============================================================================
// Replace these placeholders with your actual local 2.4 GHz Wi-Fi network credentials.
// Note: ESP32 only supports 2.4 GHz Wi-Fi (802.11 b/g/n), NOT 5 GHz networks.
#define WIFI_SSID             "YOUR_WIFI_SSID"       ///< Wi-Fi network name (2.4 GHz)
#define WIFI_PASSWORD         "YOUR_WIFI_PASSWORD"   ///< Wi-Fi network password
#define WIFI_CONNECT_TIMEOUT  15000                  ///< Wi-Fi connection timeout in milliseconds (15s)
#define WIFI_RETRY_INTERVAL   10000                  ///< Interval between reconnection attempts in ms (10s)

// =============================================================================
// 3. FLASK BACKEND SERVER CONFIGURATION
// =============================================================================
// Enter the LAN IP address or hostname of the PC running the Flask backend.
// Example: "192.168.1.100" (do NOT use "127.0.0.1" or "localhost" as ESP32 is a separate device)
#define SERVER_HOST           "192.168.1.100"        ///< Flask server IP on your local network
#define SERVER_PORT           5000                   ///< Flask server port (default: 5000)
#define API_SCAN_ENDPOINT     "/api/scans"           ///< Official backend scan endpoint (POST /api/scans)
#define HTTP_REQUEST_TIMEOUT  6000                   ///< HTTP request timeout in milliseconds (6s)

// Mandatory Hardware Device Authentication
// A valid device API key (minimum 32 characters) is MANDATORY for hardware requests to /api/scans.
// The backend server validates X-Device-Id and X-API-Key against configured DEVICE_API_KEYS.
// If left empty ("") or shorter than 32 characters, the firmware api_client will abort scan requests locally.
// If an invalid or unrecognized key is sent, the backend strictly rejects requests with HTTP 401 Unauthorized.
// Keep empty as a placeholder in version control; set to your pre-shared secret key before deployment.
#define DEVICE_API_KEY        ""                     ///< Pre-shared Device API Key (mandatory, min 32 chars)

// =============================================================================
// 4. RC522 RFID READER PIN DEFINITIONS (SPI BUS)
// =============================================================================
// Standard VSPI pins on ESP32 DevKit V1:
// - SCK:  GPIO 18
// - MISO: GPIO 19
// - MOSI: GPIO 23
// - SDA:  GPIO 27 (Chip Select / SS - avoids GPIO5 bootstrapping and boot glitches)
// - RST:  GPIO 4  (Reset pin)
// - VCC:  3.3V    (DO NOT connect to 5V! RC522 is a 3.3V device)
// - GND:  GND
#define RFID_SS_PIN           27                     ///< RC522 Chip Select (SDA/SS)
#define RFID_RST_PIN          4                      ///< RC522 Reset (RST)
#define RFID_SCK_PIN          18                     ///< SPI Clock (SCK)
#define RFID_MISO_PIN         19                     ///< SPI Master-In-Slave-Out (MISO)
#define RFID_MOSI_PIN         23                     ///< SPI Master-Out-Slave-In (MOSI)

// =============================================================================
// 5. SSD1306 OLED DISPLAY PIN DEFINITIONS (I2C BUS)
// =============================================================================
// Standard I2C pins on ESP32 DevKit V1:
// - SDA: GPIO 21
// - SCL: GPIO 22
// - VCC: 3.3V
// - GND: GND
#define OLED_SDA_PIN          21                     ///< I2C Data (SDA)
#define OLED_SCL_PIN          22                     ///< I2C Clock (SCL)
#define OLED_I2C_ADDRESS      0x3C                   ///< Standard I2C address for 0.96" SSD1306 (0x3C or 0x3D)
#define SCREEN_WIDTH          128                    ///< OLED display width in pixels
#define SCREEN_HEIGHT         64                     ///< OLED display height in pixels
#define OLED_RESET_PIN        -1                     ///< Reset pin (-1 if sharing ESP32 reset)

// =============================================================================
// 6. ACTIVE BUZZER CONFIGURATION
// =============================================================================
#define BUZZER_PIN            25                     ///< Buzzer control signal GPIO
#define BUZZER_ENABLED        true                   ///< Set to false to disable audible feedback
#define BUZZER_ACTIVE_HIGH    true                   ///< True if active-HIGH module, false if active-LOW module

/**
 * ELECTRICAL SAFETY WARNING:
 * The ESP32 GPIO pins can safely source a maximum of 12 mA (recommended continuous,
 * absolute maximum 40 mA per Espressif specifications). Direct overcurrent can permanently
 * damage the ESP32 silicon.
 * 
 * Many electromagnetic buzzers draw 25-50 mA during operation:
 * - CAUTION: A 3-pin buzzer module is NOT automatically safe for direct GPIO connection.
 *   Many generic 3-pin breakout modules lack an onboard switching transistor or pull excessive
 *   current directly from the signal pin. Always inspect the module to verify that an active
 *   driver circuit (e.g., an SMD NPN/PNP transistor such as S8050/SS8550) is present and that
 *   the control pin input current is well under 12 mA at 3.3V logic levels.
 * - If using a raw 2-pin electromagnetic buzzer or an unbuffered 3-pin module, DO NOT wire it
 *   directly to GPIO 25. Instead, use an external NPN transistor switching circuit (e.g., 2N2222,
 *   BC547, or S8050) with a 1k Ohm base resistor and a 1N4148 flyback diode across the buzzer
 *   terminals to absorb inductive voltage spikes.
 */

// =============================================================================
// 7. SCAN TIMING & DEBOUNCE SETTINGS
// =============================================================================
#define SCAN_DEBOUNCE_MS      2000                   ///< Minimum delay between scans of different cards (2s)
#define SAME_CARD_HOLD_MS     5000                   ///< Hold debounce: ignore repeated scans of same UID for 5s
#define FEEDBACK_DISPLAY_MS   3500                   ///< Duration to show attendance result on OLED (3.5s)

// =============================================================================
// 8. SERIAL & DEBUG SETTINGS
// =============================================================================
#define SERIAL_BAUD_RATE      115200                 ///< Serial monitor baud rate
#define DEBUG_ENABLED         true                   ///< Set to true for verbose serial logging

#if DEBUG_ENABLED
  #define DEBUG_PRINT(x)       Serial.print(x)
  #define DEBUG_PRINTLN(x)     Serial.println(x)
  #define DEBUG_PRINTF(...)    Serial.printf(__VA_ARGS__)
#else
  #define DEBUG_PRINT(x)
  #define DEBUG_PRINTLN(x)
  #define DEBUG_PRINTF(...)
#endif

#endif // CONFIG_H

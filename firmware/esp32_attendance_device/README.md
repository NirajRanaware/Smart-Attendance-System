# ESP32 Smart Attendance Device — Firmware (Milestone 1)

This directory contains the modular Arduino-based firmware for the physical **IoT-Based Smart Attendance Management System** scanning terminal.

---

## 1. Hardware Overview

| Component | Specification | Interface | Operating Voltage |
| :--- | :--- | :--- | :--- |
| **Microcontroller** | ESP32-WROOM-32 (ESP32 DevKit V1, 30-pin/38-pin) | USB-UART / Wi-Fi | 3.3V (5V via USB VIN) |
| **RFID Reader** | NXP MFRC522 (13.56 MHz) | Hardware SPI (VSPI) | **3.3V ONLY** (DO NOT connect to 5V!) |
| **RFID Tags** | MIFARE Classic 1K Cards / Keyfobs (13.56 MHz) | Contactless ISO 14443A | Passive |
| **Display** | 0.96-inch Monochrome OLED (SSD1306, 128×64) | Hardware I2C | 3.3V |
| **Acoustic Indicator** | Active Buzzer (5V or 3.3V module) | Digital GPIO 25 | 3.3V logic (see driver note) |

---

## 2. Complete Pin Configuration & Wiring Reference

### 2.1 RC522 RFID Reader (SPI Bus)
> [!CAUTION]
> The RC522 reader requires a **3.3V supply**. Connecting VCC to the 5V/VIN pin will permanently damage the RC522 chip.

| RC522 Pin | ESP32 GPIO | Description |
| :--- | :--- | :--- |
| **SDA / SS** | **GPIO 5** | SPI Chip Select (Slave Select) |
| **SCK** | **GPIO 18** | SPI Clock |
| **MOSI** | **GPIO 23** | SPI Master-Out-Slave-In |
| **MISO** | **GPIO 19** | SPI Master-In-Slave-Out |
| **RST** | **GPIO 4** | Hardware Reset Pin |
| **VCC** | **3.3V** | 3.3V Power Rail |
| **GND** | **GND** | Common Ground |
| **IRQ** | *Unconnected* | Interrupt pin (not required in polling mode) |

### 2.2 SSD1306 128×64 OLED Display (I2C Bus)

| OLED Pin | ESP32 GPIO | Description |
| :--- | :--- | :--- |
| **SDA** | **GPIO 21** | I2C Data Line |
| **SCL** | **GPIO 22** | I2C Clock Line |
| **VCC** | **3.3V** | 3.3V Power Rail |
| **GND** | **GND** | Common Ground |

### 2.3 Buzzer Interface & Electrical Safety

| Buzzer Connection | ESP32 Pin | Description |
| :--- | :--- | :--- |
| **Signal (I/O)** | **GPIO 25** | Digital control output (Active HIGH/LOW configurable in `config.h`) |
| **VCC** | **3.3V or 5V** | Power rail (match buzzer rating) |
| **GND** | **GND** | Common Ground |

> [!WARNING]
> **GPIO Current Limit Warning:**
> ESP32 GPIO pins are rated for a continuous output current of **12 mA** (40 mA absolute maximum). Many active electromagnetic buzzers draw **25 mA to 50 mA**.
> - **3-Pin Buzzer Modules:** A 3-pin module is NOT automatically safe for direct GPIO connection. Many generic modules lack an onboard switching transistor or draw excessive current. Verify that the module incorporates an onboard active transistor driver (e.g., S8050/SS8550) and that the control pin signal current remains well under 12 mA at 3.3V logic levels before connecting directly to GPIO 25.
> - **Raw 2-pin Buzzers & Unbuffered Modules:** DO NOT connect directly between GPIO 25 and GND. Use an external NPN transistor switching circuit (e.g., 2N2222, BC547, or S8050) with a 1 kOhm base resistor and a 1N4148 flyback diode across the buzzer coil to prevent inductive back-EMF spikes.

---

## 3. Directory Structure

```text
firmware/esp32_attendance_device/
├── esp32_attendance_device.ino  # Main sketch entry point (setup and loop state machine)
├── config.h                     # Central pin definitions, network and timing settings
├── display_manager.h            # OLED display controller header (SSD1306 128x64)
├── display_manager.cpp          # OLED rendering routines for 13 distinct system states
├── rfid_scanner.h               # RC522 RFID reader controller header
├── rfid_scanner.cpp             # SPI reader initialization, UID formatting & hold debounce
├── wifi_manager.h               # Wi-Fi network manager header
├── wifi_manager.cpp             # Wi-Fi station mode & non-blocking auto-reconnection
├── api_client.h                 # Backend HTTP REST client header
├── api_client.cpp               # POST /api/scans communication & JSON response parsing
├── buzzer.h                     # Acoustic feedback controller header
├── buzzer.cpp                   # Non-blocking audio cues for scan events & error states
└── README.md                    # Setup, compilation, and troubleshooting guide
```

---

## 4. Required Libraries

Install the following libraries via the Arduino Library Manager or PlatformIO:

| Library Name | Author | Recommended Version | Purpose |
| :--- | :--- | :--- | :--- |
| **MFRC522** | GithubCommunity (miguelbalboa) | `^1.4.11` | SPI driver for NXP MFRC522 RFID reader |
| **Adafruit SSD1306** | Adafruit | `^2.5.9` | I2C driver for monochrome 128x64 OLED display |
| **Adafruit GFX Library** | Adafruit | `^1.11.9` | Core graphics primitives and typography |
| **ArduinoJson** | Benoît Blanchon | `^6.21.5` or `^7.x` | Memory-efficient JSON serialization & deserialization |
| **WiFi** | Espressif Systems | Built-in | ESP32 core Wi-Fi stack |
| **HTTPClient** | Espressif Systems | Built-in | ESP32 core HTTP 1.1 client |
| **Wire & SPI** | Espressif Systems | Built-in | Hardware I2C and SPI bus management |

---

## 5. Configuration Steps

### 5.1 First-Time Setup (Clean Clone)
The firmware repository tracks a clean template file (`config.example.h`). The local configuration file (`config.h`) contains private network credentials and is ignored by Git to prevent accidental credential leakage.

To configure your device:
1. Open PowerShell and navigate to the firmware directory:
   ```powershell
   cd firmware\esp32_attendance_device
   ```
2. Copy the template to create your local `config.h`:
   ```powershell
   Copy-Item config.example.h config.h
   ```
3. Open `config.h` in your editor and configure your local environment settings.
4. **Never commit or upload `config.h` to version control.** It is automatically excluded by `.gitignore`.

### 5.2 Required Parameters in config.h
Before uploading the firmware, configure your local environment settings in `config.h`:

```cpp
// 1. Wi-Fi Settings (2.4 GHz only)
#define WIFI_SSID             "YOUR_WIFI_SSID"
#define WIFI_PASSWORD         "YOUR_WIFI_PASSWORD"

// 2. Flask Backend Server IP & Port
// Note: Use your development PC's local LAN IP (e.g. 192.168.1.100), NOT "127.0.0.1"
#define SERVER_HOST           "192.168.1.100"
#define SERVER_PORT           5000

// 3. Hardware Device Authentication (Mandatory)
#define DEVICE_ID             "ESP32_ATTENDANCE_01"
#define DEVICE_API_KEY        ""  // Must match backend DEVICE_API_KEYS (min 32 chars)

// 4. Buzzer Configuration
#define BUZZER_ACTIVE_HIGH    true    // Set to false if using an active-LOW buzzer module
```

---

## 6. How to Build & Upload

### Option A: Using Arduino IDE

1. **Install ESP32 Board Support:**
   - In Arduino IDE, go to **File** $\rightarrow$ **Preferences**.
   - In *Additional Board Manager URLs*, add:
     `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Open **Tools** $\rightarrow$ **Board** $\rightarrow$ **Boards Manager**, search for `esp32` by Espressif Systems, and click **Install**.
2. **Install Required Libraries:**
   - Go to **Sketch** $\rightarrow$ **Include Library** $\rightarrow$ **Manage Libraries...**
   - Search and install: `MFRC522`, `Adafruit SSD1306`, `Adafruit GFX Library`, and `ArduinoJson`.
3. **Select Board & Port:**
   - **Board:** Select **DOIT ESP32 DEVKIT V1** (or *ESP32 Dev Module*).
   - **Upload Speed:** `921600` (or `115200` if upload fails).
   - **Port:** Select the COM port assigned to the CP2102/CH340 USB-UART bridge.
4. **Compile & Upload:**
   - Click the **Verify** (checkmark) button to compile.
   - Click the **Upload** (arrow) button to flash the firmware.
   - *Note:* If the upload hangs at `Connecting........_____.....`, press and hold the **BOOT** button on the ESP32 until the flashing progress percentage appears.

### Option B: Using PlatformIO (VS Code or CLI)

Create a `platformio.ini` file in the firmware root:

```ini
[env:esp32doit-devkit-v1]
platform = espressif32
board = esp32doit-devkit-v1
framework = arduino
monitor_speed = 115200
upload_speed = 921600
lib_deps =
    miguelbalboa/MFRC522@^1.4.11
    adafruit/Adafruit SSD1306@^2.5.9
    adafruit/Adafruit GFX Library@^1.11.9
    bblanchon/ArduinoJson@^7.0.4
```

To compile and upload via terminal:
```bash
pio run --target upload
pio device monitor
```

---

## 7. Backend API Contract Summary

The firmware communicates directly with the Flask backend's official scan endpoint:

* **Endpoint:** `POST /api/scans`
* **Content-Type:** `application/json`
* **Request Payload:**
  ```json
  {
    "card_uid": "A1B2C3D4",
    "source": "ESP32_ATTENDANCE_01"
  }
  ```
* **Success Response (HTTP 201):**
  ```json
  {
    "success": true,
    "scan_result": "SUCCESS",
    "student": {
      "roll_number": "101",
      "student_name": "Rahul Patil"
    },
    "attendance": {
      "status": "Present"
    }
  }
  ```
* **Duplicate Response (HTTP 409):**
  ```json
  {
    "success": false,
    "scan_result": "DUPLICATE",
    "student": {
      "roll_number": "101",
      "student_name": "Rahul Patil"
    }
  }
  ```

---

## 8. Troubleshooting Guide

| Symptom | Probable Cause | Corrective Action |
| :--- | :--- | :--- |
| **OLED display is completely black** | Incorrect I2C address or wiring | Verify SDA $\rightarrow$ GPIO 21 and SCL $\rightarrow$ GPIO 22. In `config.h`, check if OLED address is `0x3C` or `0x3D`. |
| **Serial shows `MFRC522 Version: 0x00` or `0xFF`** | SPI wiring error or low voltage | Ensure RC522 VCC is connected to **3.3V** (not 5V). Double-check SCK (18), MISO (19), MOSI (23), and SS (5). |
| **Wi-Fi connection times out** | 5 GHz network or weak signal | Ensure the Wi-Fi router broadcasts a 2.4 GHz SSID. ESP32 does not support 5 GHz networks. |
| **Server Error: HTTP 401 Unauthorized** | Device Authentication Failed | 1. Confirm `DEVICE_ID` matches your configured device identifier.<br>2. Confirm `DEVICE_API_KEY` meets the minimum 32-character requirement.<br>3. Confirm backend `DEVICE_API_KEYS` mapping in `.env` / environment contains this `DEVICE_ID` and exact matching key.<br>4. Confirm the ESP32 can reach the Flask server over the LAN.<br>5. Never disable authentication or CSRF controls (`LOGIN_DISABLED=True` is strictly prohibited). |
| **Server Error: Connection Refused / Timeout** | Wrong server IP or firewall | Ensure PC and ESP32 are on the same Wi-Fi subnet. Verify PC firewall allows incoming connections on port 5000. |

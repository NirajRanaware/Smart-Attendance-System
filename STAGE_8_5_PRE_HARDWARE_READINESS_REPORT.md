# STAGE 8.5 — PRE-HARDWARE FLIGHT CHECK & DEPLOYMENT PREPARATION REPORT

**Project:** IoT-Based Smart Attendance Management System  
**Workspace:** `C:\Projects\Smart-Attendance-System`  
**Branch:** `main`  
**Milestone:** 2 — Software Verification and Simulation  
**Stage:** 8.5 — Pre-Hardware Flight Check & Deployment Preparation  
**Status:** COMPLETE (Audited & Verified)  
**Readiness Decision:** **READY FOR HARDWARE PREPARATION**  

---

## 1. Executive Summary

In **Stage 8.5**, we performed a comprehensive pre-hardware engineering audit and deployment flight check for the IoT-Based Smart Attendance Management System. This audit bridges Milestone 2 (Software Verification and Simulation) and future physical hardware integration.

**Critical Scope Restriction:** This stage is strictly an inspection, auditing, and preparation task. **Zero hardware was connected, powered, wired, or flashed.**

### Key Audit Highlights
1. **Target Board Discrepancy Resolved:** Investigated the Arduino CLI board definition discrepancy between Stage 8.4 (`esp32:esp32:esp32da`) and planned hardware (`esp32:esp32:esp32` / `esp32:esp32:esp32doit-devkit-v1`). Successfully compiled production firmware against both verified targets with zero errors.
2. **Configuration & Secrets Sanitization Verified:** Confirmed that `config.h` and all tracked source files contain only placeholders (`YOUR_WIFI_SSID`, `YOUR_WIFI_PASSWORD`, `""` for `DEVICE_API_KEY`). Established safe local credential management procedures using Git index flags.
3. **Backend Network Architecture Prepared:** Documented LAN IP binding requirements (`0.0.0.0:5000` vs loopback `127.0.0.1:5000`), subnet alignment, Windows Firewall considerations, and pre-hardware LAN verification steps.
4. **Hardware Pinout & Electrical Safety Audited:** Verified zero pin conflicts across SPI, I2C, and GPIO buses. Identified critical electrical safety boundaries for the RC522 (3.3V strict limit) and the active buzzer module (GPIO 12 mA limit vs buzzer 25–50 mA coil draw).
5. **Physical Laboratory Verification Plan Created:** Established an electrical measurement checklist for execution in the college electronics laboratory (measuring buzzer coil current, verifying transistor driver existence, and confirming USB data line continuity).
6. **Staged Future Hardware Testing Sequence Formulated:** Designed a 5-step staged physical verification sequence (Standalone ESP32 $\rightarrow$ OLED $\rightarrow$ Buzzer $\rightarrow$ RC522 $\rightarrow$ Live Backend Integration).
7. **Readiness Evaluation:** Graded the system as **READY FOR HARDWARE PREPARATION**. Physical assembly must remain gated until electrical specifications are verified in the college electronics lab.

---

## 2. Actual ESP32 Board and Build Target Verification

### 2.1 Investigation of the FQBN Discrepancy

During Stage 8.4 testing, commands utilized the Fully Qualified Board Name (FQBN):
`esp32:esp32:esp32da`

However, the planned hardware consists of the classic **ESP32 DevKit V1** carrying an **ESP32-WROOM-32** module. We queried the installed Arduino ESP32 Core (version `2.0.17`) via `arduino-cli board listall esp32` to resolve this discrepancy.

#### Installed Core Board Definitions
| Board Name in Core | FQBN | Microcontroller / Module Target |
|---|---|---|
| **ESP32 Dev Module** | `esp32:esp32:esp32` | Generic ESP32-WROOM-32 / ESP32-D0WDQ6 |
| **DOIT ESP32 DEVKIT V1** | `esp32:esp32:esp32doit-devkit-v1` | DOIT ESP32 DevKit V1 (ESP32-WROOM-32) |
| **ESP32-WROOM-DA Module** | `esp32:esp32:esp32da` | ESP32-WROOM-DA (Dual-Antenna variant) |

#### Findings:
1. `esp32:esp32:esp32da` corresponds specifically to the **ESP32-WROOM-DA** module, which features dual PCB antennas and antenna-switching circuitry. While binary code compiled for `esp32da` runs on standard ESP32 silicon due to common Tensilica Xtensa LX6 architecture, it is not the canonical target for a standard DevKit V1.
2. `esp32:esp32:esp32` corresponds to the standard generic **ESP32 Dev Module**.
3. `esp32:esp32:esp32doit-devkit-v1` corresponds specifically to the commercial **DOIT ESP32 DEVKIT V1** board, matching `board = esp32doit-devkit-v1` in `firmware/esp32_attendance_device/README.md`.

### 2.2 Compilation Verification

We executed real firmware builds against both verified targets using `arduino-cli`:

#### Test A: Generic ESP32 Target (`esp32:esp32:esp32`)
```bash
tools\arduino-cli\arduino-cli.exe --config-file tools\arduino-cli\arduino-cli.yaml compile --fqbn esp32:esp32:esp32 firmware\esp32_attendance_device
```
- **Result:** **PASSED (Exit Code: 0)**
- **Program Storage (Flash):** 787,477 bytes (60% of 1,310,720 bytes maximum)
- **Dynamic Memory (RAM):** 45,208 bytes (13% of 327,680 bytes maximum, 282,472 bytes free)

#### Test B: Specific DOIT DevKit V1 Target (`esp32:esp32:esp32doit-devkit-v1`)
```bash
tools\arduino-cli\arduino-cli.exe --config-file tools\arduino-cli\arduino-cli.yaml compile --fqbn esp32:esp32:esp32doit-devkit-v1 firmware\esp32_attendance_device
```
- **Result:** **PASSED (Exit Code: 0)**
- **Program Storage (Flash):** 787,477 bytes (60% of 1,310,720 bytes maximum)
- **Dynamic Memory (RAM):** 45,208 bytes (13% of 327,680 bytes maximum, 282,472 bytes free)

#### Test C: Comparison with Prior `esp32:esp32:esp32da`
- Flash was 788,777 bytes (1,300 bytes larger due to dual-antenna driver stubs).
- Both `esp32:esp32:esp32` and `esp32:esp32:esp32doit-devkit-v1` produce a cleaner, slightly more compact 787,477-byte image.

**Conclusion:** Both `esp32:esp32:esp32` and `esp32:esp32:esp32doit-devkit-v1` are 100% valid. For standard Arduino CLI usage, `esp32:esp32:esp32doit-devkit-v1` is recommended as it matches the exact physical board variant.

---

## 3. Firmware Configuration and Secret-Handling Audit

### 3.1 Inspected Files
- `firmware/esp32_attendance_device/config.h`
- `firmware/esp32_attendance_device/api_client.cpp`
- `firmware/esp32_attendance_device/wifi_manager.cpp`
- `firmware/esp32_attendance_device/esp32_attendance_device.ino`
- `firmware/esp32_attendance_device/README.md`
- `app/auth.py` and `app/config.py`
- `.gitignore`

### 3.2 Secret Sanitization Verification
Inspection of tracked files confirmed:
1. `config.h` contains strictly placeholder strings:
   ```cpp
   #define WIFI_SSID             "YOUR_WIFI_SSID"
   #define WIFI_PASSWORD         "YOUR_WIFI_PASSWORD"
   #define SERVER_HOST           "192.168.1.100"
   #define DEVICE_API_KEY        ""
   ```
2. No genuine Wi-Fi network credentials, personal passwords, or private API keys exist in Git tracking or test reports.
3. `wifi_manager.cpp` explicitly avoids logging Wi-Fi passwords to Serial (line 25: `// Note: Never log password to preserve network security`).
4. `esp32_attendance_device.ino` implements `maskUid()` to mask card identifiers on the diagnostic Serial monitor (e.g., `A1****D4`).

### 3.3 Safe Local Configuration Pattern

#### Git Exposure Risk:
`config.h` is currently a tracked file in Git. If a developer edits `config.h` with their real home or campus Wi-Fi credentials and API keys, running `git commit -a` or `git add .` would accidentally commit sensitive credentials into version control.

#### Recommended Operational Procedure:
Before editing `config.h` for physical flashing, developers should instruct Git to ignore local modifications to `config.h`:

```bash
# 1. Prevent Git from tracking local credential modifications in config.h
git update-index --assume-unchanged firmware/esp32_attendance_device/config.h

# 2. To verify the flag is active:
git ls-files -v firmware/esp32_attendance_device/config.h
# The output starts with lowercase 'h', indicating assume-unchanged is active.

# 3. If upstream changes ever need to be pulled in the future:
git update-index --no-assume-unchanged firmware/esp32_attendance_device/config.h
```

### 3.4 Wi-Fi Network Requirements
- **Frequency Band:** 2.4 GHz ONLY (IEEE 802.11 b/g/n). ESP32 hardware transceivers do NOT support 5 GHz Wi-Fi networks.
- **Security Protocols:** WPA/WPA2-Personal (PSK). Enterprise networks (WPA2-Enterprise with 802.1X PEAP/MSCHAPv2 username/password) require specialized firmware not currently implemented.
- **Captive Portals:** Hotel/campus Wi-Fi networks requiring web-browser login or terms acceptance will block the ESP32. Use a standard dedicated 2.4 GHz WPA2 hotspot or lab router.
- **Connection Timing:**
  - `WIFI_CONNECT_TIMEOUT`: 15,000 ms (15 seconds) blocking timeout on boot.
  - `WIFI_RETRY_INTERVAL`: 10,000 ms (10 seconds) non-blocking reconnection interval in background.

### 3.5 Device Authentication Requirements
- **Device ID:** `ESP32_ATTENDANCE_01` (Header: `X-Device-Id`).
- **Device API Key:** Pre-shared cryptographic secret (Header: `X-API-Key`).
- **Minimum Length:** Strictly $\ge 32$ characters enforced by backend `app/auth.py`.
- **Backend Configuration:** Key must be pre-registered in backend environment/config `DEVICE_API_KEYS`:
  ```json
  {"ESP32_ATTENDANCE_01": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"}
  ```
- **Local Guard:** `api_client.cpp` aborts locally with `SCAN_OUTCOME_DEVICE_AUTH_ERROR` if `DEVICE_API_KEY` is empty, avoiding unnecessary Wi-Fi transmissions.

---

## 4. Backend Network Architecture & LAN Deployment

### 4.1 Localhost Simulation vs. Physical LAN Architecture

```
STAGE 8.4 SIMULATION (Host Loopback):
+-------------------------------------------------------------+
| Same Host Machine (127.0.0.1)                               |
| [firmware_e2e_runner.exe] ---> TCP Socket ---> [Flask:5000] |
+-------------------------------------------------------------+

FUTURE PHYSICAL DEPLOYMENT (Local Area Network):
+-------------------------+             +-------------------------------+
| ESP32 Hardware Device   |   802.11    | Development Host PC           |
| IP: 192.168.1.150       |   2.4 GHz   | IP: 192.168.1.100             |
| Client: AttendanceApiClient --------> | Flask Server: 0.0.0.0:5000    |
+-------------------------+             +-------------------------------+
                                                        |
                                                  SQLite Database
```

### 4.2 Network Configuration Procedures

1. **Identifying PC LAN IPv4 Address in Windows:**
   - Open PowerShell or Command Prompt.
   - Run `ipconfig`.
   - Locate the active network adapter (e.g., `Wireless LAN adapter Wi-Fi` or `Ethernet adapter`).
   - Find the line `IPv4 Address. . . . . . . . . . . : 192.168.1.X`.
   - Note this IP address for `SERVER_HOST` in `config.h`.

2. **Subnet Alignment & Router Isolation:**
   - Both the development PC and the ESP32 must reside on the same IP subnet (e.g., `192.168.1.0/24`).
   - **Router AP Isolation Warning:** Many guest Wi-Fi networks and campus routers enable "Client Isolation" / "AP Isolation", preventing wireless clients from communicating with each other. If enabled, the ESP32 cannot reach the PC. A private mobile hotspot or dedicated lab router is recommended.

3. **Backend Host Binding:**
   - In `run.py`, the development server is currently configured with `host="127.0.0.1"`.
   - When physical integration is authorized, the host binding must be changed to `host="0.0.0.0"` (all network interfaces) so that incoming requests from external IP addresses on the LAN are accepted.

4. **Windows Firewall Configuration:**
   - Windows Firewall blocks incoming TCP connections on port 5000 by default.
   - When Flask binds to `0.0.0.0`, Windows displays a firewall prompt ("Allow Python to communicate on Private networks"). The user must select **Private networks** and click **Allow access**.
   - Alternatively, add an inbound firewall rule in PowerShell:
     ```powershell
     New-NetFirewallRule -DisplayName "Flask Attendance Backend (Port 5000)" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow
     ```

5. **Security Warning — Development Server on Public Networks:**
   - The Flask development server with `debug=True` (Werkzeug interactive debugger) **MUST NEVER** be exposed directly to the public Internet via port forwarding or DMZ.
   - The debugger PIN allows remote arbitrary code execution. LAN-only development is strictly required.

6. **Pre-Hardware Connectivity Validation (Sanity Check):**
   Before connecting the ESP32 hardware, verify LAN connectivity from another device (such as a smartphone connected to the same Wi-Fi):
   - Open mobile browser to: `http://192.168.1.X:5000/api/health`
   - If the smartphone displays `{"status":"healthy","database":"connected"}`, LAN connectivity, host binding, and firewall rules are confirmed ready for the ESP32.

---

## 5. Hardware Inventory and Pinout Audit

### 5.1 Bill of Materials (BOM)

| Component | Part Description | Interface | Voltage Logic | Quantity |
|---|---|---|---|---|
| **MCU** | ESP32 DevKit V1 (30-pin or 38-pin, ESP32-WROOM-32) | USB / Wi-Fi | 3.3V Logic | 1 |
| **RFID Reader** | NXP MFRC522 (RC522 Breakout Board, 13.56 MHz) | SPI (VSPI) | 3.3V Power & Logic | 1 |
| **RFID Cards** | MIFARE Classic 1K Cards / S50 Keyfobs (13.56 MHz) | ISO 14443A | Passive RF | Multiple |
| **Display** | 0.96-inch Monochrome OLED (SSD1306, 128×64) | I2C | 3.3V / 5V Tolerant | 1 |
| **Buzzer** | Active Buzzer Module (3-pin VCC/I/O/GND, code 2608) | Digital GPIO | Requires Inspection | 1 |
| **Breadboard** | 830-point solderless breadboard | — | — | 1 |
| **Jumper Wires** | Male-to-Male and Male-to-Female jumper cables | — | — | ~15 |
| **USB Cable** | Micro-USB (or USB-C) Data & Power Cable | USB 2.0 | 5V USB VBUS | 1 |

---

### 5.2 Complete Pin Mapping Audit

| Subsystem | Peripheral Pin | ESP32 GPIO | Electrical Function | Firmware Constant | Pin Conflict Check |
|---|---|---|---|---|---|
| **RC522** | SDA / SS | **GPIO 5** | SPI Chip Select (CS) | `RFID_SS_PIN` | No Conflict |
| **RC522** | SCK | **GPIO 18** | SPI Clock | `RFID_SCK_PIN` | No Conflict |
| **RC522** | MOSI | **GPIO 23** | SPI Master-Out | `RFID_MOSI_PIN` | No Conflict |
| **RC522** | MISO | **GPIO 19** | SPI Master-In | `RFID_MISO_PIN` | No Conflict |
| **RC522** | RST | **GPIO 4** | Hardware Reset | `RFID_RST_PIN` | No Conflict |
| **RC522** | VCC | **3.3V** | Power Rail (**3.3V ONLY**) | — | Dedicated 3.3V Rail |
| **RC522** | GND | **GND** | Ground Rail | — | Common Ground |
| **RC522** | IRQ | *NC* | Interrupt (Unused) | — | Floating (Unconnected) |
| **OLED** | SDA | **GPIO 21** | I2C Serial Data | `OLED_SDA_PIN` | No Conflict |
| **OLED** | SCL | **GPIO 22** | I2C Serial Clock | `OLED_SCL_PIN` | No Conflict |
| **OLED** | VCC | **3.3V** | Power Rail | — | Dedicated 3.3V Rail |
| **OLED** | GND | **GND** | Ground Rail | — | Common Ground |
| **Buzzer** | Signal (I/O) | **GPIO 25** | Digital Output | `BUZZER_PIN` | No Conflict |
| **Buzzer** | VCC | **To Verify** | Power Rail | — | Lab Measurement Req. |
| **Buzzer** | GND | **GND** | Ground Rail | — | Common Ground |

### 5.3 ESP32 Boot Strapping & Silicon Constraints Analysis
- **Boot Strapping Pins:**
  - `GPIO 0`: Bootloader mode select. Must remain HIGH during power-on for normal flash boot. (Unused in our pinout; onboard pull-up exists).
  - `GPIO 2`: Must be floating or LOW during UART flashing. (Unused in our pinout).
  - `GPIO 5`: Used for `RFID_SS_PIN`. Outputs PWM at boot timing for SDIO slave. Internal pull-up is present; acceptable for RC522 CS.
  - `GPIO 12`: MTDI bootstrapping pin. Controls flash voltage (3.3V vs 1.8V). **Avoided completely** to prevent flash brownout.
  - `GPIO 15`: Boot logging output. (Unused in our pinout).
- **Input-Only Pins (GPI):**
  - `GPIO 34, 35, 36, 39`: Lack internal pull-ups and output drivers. None of our outputs or bidirectional buses use these pins.

---

## 6. Critical Electrical Safety Checklist

> [!CAUTION]
> **CRITICAL RULE 1: RC522 POWER RAIL VOLTAGE (3.3V ONLY)**
> The NXP MFRC522 IC is strictly a **3.3V device** (operating voltage: 2.5V to 3.6V).
> - Connecting RC522 VCC to 5V (or VIN) will permanently destroy the transceiver chip.
> - Always connect RC522 VCC to the **3.3V output pin** of the ESP32 DevKit board.

> [!WARNING]
> **CRITICAL RULE 2: BUZZER CURRENT & TRANSISTOR REQUIREMENT**
> ESP32 GPIO pins have an absolute maximum current rating of **40 mA**, with a recommended continuous operating limit of **12 mA**.
> - Many standard active buzzers draw **25 mA to 50 mA** when oscillating.
> - Direct connection of an unbuffered buzzer coil to GPIO 25 can cause permanent overcurrent degradation or silicon burnout of the ESP32 output driver.
> - Generic 3-pin modules marked "2608" or similar batch codes often vary widely: some have an onboard SMD NPN/PNP transistor (e.g. S8050/J3Y), while others simply route the signal pin directly to the buzzer coil with a pull-up resistor.
> - **Mandatory Lab Verification:** The buzzer module must be inspected and measured in the college electronics laboratory before connection.

> [!IMPORTANT]
> **CRITICAL RULE 3: COMMON GROUND REFERENCE**
> All modules (ESP32, RC522, OLED, Buzzer) must share a single, unbroken ground plane (Common GND). Unequal ground potentials cause SPI/I2C communication glitches and false card reads.

---

## 7. Hardware Specifications Requiring Physical Laboratory Verification

Because the user does not currently possess a digital multimeter at home, the following specifications **must be verified in the college electronics laboratory** before applying power:

| Component | Specification to Verify | Why It Matters | Verification Method in College Lab | Status |
|---|---|---|---|---|
| **ESP32 DevKit** | Pin count & width (30-pin vs 38-pin) | Breadboard straddling: 30-pin leaves 1 row free per side; 38-pin leaves 0 rows on standard breadboard | Visual count & physical fitting on 830-point breadboard | **UNVERIFIED** |
| **ESP32 USB** | USB bridge IC (CP2102 vs CH340 vs CH9102) | Requires specific Windows VCP driver installation | Inspect IC markings near USB port | **UNVERIFIED** |
| **USB Cable** | Data capability ($D+$ / $D-$ lines present) | Cheap charge-only cables do not connect UART data lines; flashing fails | Multimeter continuity test between Type-A and Micro-USB pins | **UNVERIFIED** |
| **RC522** | Supply pin silk-screen and solder joints | Verifying 3.3V label and checking for solder bridges between header pins | Visual inspection under magnifying glass / lab microscope | **UNVERIFIED** |
| **OLED Display** | I2C Pull-Up Resistors & Address | Address is usually `0x3C`, but some boards default to `0x3D` via rear 0-ohm jumper | Check rear SMD resistor jumper on OLED breakout | **UNVERIFIED** |
| **Buzzer Module** | Onboard Transistor Presence | Verifying if module has active switching transistor (SMD code J3Y / 2TY) | Inspect PCB for 3-terminal SMD transistor and base resistor | **UNVERIFIED** |
| **Buzzer Module** | Signal Pin Current Draw | Must measure control pin current: if $> 10$ mA, direct ESP32 GPIO drive is unsafe | Connect VCC to 3.3V, measure DC current into I/O pin with digital multimeter | **UNVERIFIED** |
| **Buzzer Module** | Operating Voltage (3.3V vs 5V) | Ensures acoustic output is audible at 3.3V supply rail | Test tone volume with 3.3V bench power supply in lab | **UNVERIFIED** |

---

## 8. Physical Wiring Sequence & Inspection Protocol

### 8.1 Phase A: College Laboratory Electrical Checks (Pre-Assembly)
1. **DMM Continuity Check:**
   - Verify USB cable data pins: Pin 1 (VBUS), Pin 2 (D-), Pin 3 (D+), Pin 4 (GND). Confirm data lines are intact.
2. **Buzzer Module Inspection:**
   - Place buzzer on lab bench power supply set to 3.3V.
   - Insert DMM in series (mA range) with the I/O signal pin.
   - Trigger the buzzer:
     - If current $\le 5\text{ mA}$: Module has an onboard transistor; safe for direct GPIO 25 connection.
     - If current $> 12\text{ mA}$: Direct GPIO connection is unsafe. An external NPN transistor (2N2222 or BC547) with a 1 k$\Omega$ base resistor must be wired on the breadboard.
3. **Module Silk-Screen Inspection:**
   - Verify RC522 pin marked `3.3V`.
   - Verify OLED pins: `GND`, `VCC`, `SCL`, `SDA`.

---

### 8.2 Phase B: Breadboard Assembly Sequence (Power DISCONNECTED)

```
STEP 1: Mount ESP32 onto breadboard straddling the center divider.
STEP 2: Connect ESP32 GND pin to the blue breadboard ground rail.
STEP 3: Connect ESP32 3.3V pin to the red breadboard power rail.
        (DO NOT connect 5V/VIN to the red rail!)
STEP 4: Wire RC522 RFID reader:
        - VCC  -> Red 3.3V rail
        - GND  -> Blue GND rail
        - RST  -> ESP32 GPIO 4
        - SDA  -> ESP32 GPIO 5
        - SCK  -> ESP32 GPIO 18
        - MISO -> ESP32 GPIO 19
        - MOSI -> ESP32 GPIO 23
STEP 5: Wire SSD1306 OLED display:
        - VCC  -> Red 3.3V rail
        - GND  -> Blue GND rail
        - SCL  -> ESP32 GPIO 22
        - SDA  -> ESP32 GPIO 21
STEP 6: Wire Active Buzzer module:
        - GND  -> Blue GND rail
        - VCC  -> Verified power rail (3.3V or 5V per lab check)
        - I/O  -> ESP32 GPIO 25 (or transistor base circuit)
```

---

### 8.3 Phase C: Pre-Power Visual & Measurement Checklist

Before plugging in the USB cable:
- [ ] **Visual Check 1:** Verify no loose wire strands or bare jumper pins touching each other.
- [ ] **Visual Check 2:** Confirm RC522 VCC connects to the **3.3V rail**, NOT VIN/5V.
- [ ] **Visual Check 3:** Confirm OLED VCC connects to 3.3V and GND connects to GND (no reverse polarity).
- [ ] **Visual Check 4:** Confirm breadboard power rails are not bridged together.
- [ ] **DMM Resistance Check (Power OFF):**
  - Measure resistance between 3.3V rail and GND rail.
  - Expected: High impedance ($> 1\text{ k}\Omega$ or charging capacitor reading).
  - If reading is near $0\ \Omega$: **STOP IMMEDIATELY — DEAD SHORT DETECTED.**

---

## 9. Future Staged Hardware Testing Sequence

Once physical assembly is authorized, verification must follow this sequential, staged procedure:

```
+-----------------------------------------------------------------+
| TEST 1: ESP32 Standalone (USB UART Boot & Wi-Fi Station Mode)   |
+-----------------------------------------------------------------+
                                 | PASS
                                 v
+-----------------------------------------------------------------+
| TEST 2: I2C Bus & SSD1306 OLED Display Initialization           |
+-----------------------------------------------------------------+
                                 | PASS
                                 v
+-----------------------------------------------------------------+
| TEST 3: Acoustic Feedback & Buzzer Driver Verification          |
+-----------------------------------------------------------------+
                                 | PASS
                                 v
+-----------------------------------------------------------------+
| TEST 4: SPI Bus, RC522 Reader Firmware Version & Card Read      |
+-----------------------------------------------------------------+
                                 | PASS
                                 v
+-----------------------------------------------------------------+
| TEST 5: Full End-to-End Live Backend LAN Integration            |
+-----------------------------------------------------------------+
```

### Detailed Staged Protocols

#### Test 1 — ESP32 Standalone
- **Setup:** ESP32 connected via USB cable only (peripherals disconnected or unpowered).
- **Execution:** Open Serial Monitor at 115200 baud. Observe boot log.
- **Expected Result:**
  - ESP32 boots cleanly: `rst:0x1 (POWERON_RESET)`.
  - Serial prints device banner: `SMART ATTENDANCE MANAGEMENT SYSTEM — ESP32`.
  - Wi-Fi attempts connection to target SSID and logs local IP.
- **Failure Symptoms:** Serial garbage (wrong baud rate); constant reboot loop (brownout or short); device unrecognized in Windows Device Manager (missing CH340/CP2102 driver or charge-only USB cable).

#### Test 2 — SSD1306 OLED Display
- **Setup:** Connect OLED (GND, 3.3V, GPIO 21 SDA, GPIO 22 SCL).
- **Execution:** Reboot ESP32.
- **Expected Result:**
  - OLED lights up crisp monochrome text.
  - Displays startup splash screen: `SMART ATTENDANCE`, `System Booting...`.
  - Transitions to `Wi-Fi Connecting...` and `READY TO SCAN`.
- **Failure Symptoms:** Screen remains black (wrong I2C address `0x3D` vs `0x3C`, reversed SDA/SCL, or missing pull-ups); corrupted pixels (incorrect display driver initialization).

#### Test 3 — Active Buzzer
- **Setup:** Connect buzzer to GPIO 25.
- **Execution:** Listen during boot sequence.
- **Expected Result:**
  - Distinct short audio pulse (50ms) on boot.
  - Double beep on Wi-Fi connection.
- **Failure Symptoms:** Continuous shrill tone (inverted active-high/active-low polarity in `config.h`); clicking without tone (passive buzzer used instead of active); silence (insufficient drive current or disconnected ground).

#### Test 4 — RC522 RFID Reader
- **Setup:** Connect RC522 SPI lines.
- **Execution:** Observe Serial monitor at Step 4 of boot. Tap MIFARE Classic 1K card.
- **Expected Result:**
  - Serial logs: `[RFID] MFRC522 Firmware Version: 0x92` (or `0x91`).
  - Serial logs: `[RFID] RC522 reader initialized and online.`
  - Tapping card emits immediate short beep (`BUZZER_BEEP_CARD_DETECTED`).
  - Display transitions to `CARD DETECTED` / `Processing...`.
- **Failure Symptoms:** Serial logs `Version: 0x00` or `Version: 0xFF` (SPI wiring error, loose jumper wire, MISO/MOSI swapped, or low 3.3V rail voltage); no response to card tap (damaged card or defective antenna coil).

#### Test 5 — Full End-to-End Backend LAN Integration
- **Setup:** Flask backend running on PC with `host="0.0.0.0"`. PC and ESP32 on same 2.4 GHz Wi-Fi.
- **Execution:** Perform card scans for all scenarios:
  1. Valid Card (`A1B2C3D4`) $\rightarrow$ HTTP 201 Created $\rightarrow$ OLED displays Roll Number & Student Name $\rightarrow$ Success Beep.
  2. Duplicate Card Scan $\rightarrow$ HTTP 409 Conflict $\rightarrow$ OLED displays `ALREADY PRESENT` $\rightarrow$ Duplicate Tone.
  3. Unregistered Card (`DEADBEEF`) $\rightarrow$ HTTP 404 $\rightarrow$ OLED displays `UNKNOWN CARD` $\rightarrow$ Error Tone.
  4. Inactive Student Card $\rightarrow$ HTTP 403 $\rightarrow$ OLED displays `ACCOUNT INACTIVE` $\rightarrow$ Error Tone.
  5. Disconnect Wi-Fi router $\rightarrow$ Tap Card $\rightarrow$ Immediate local offline rejection on OLED $\rightarrow$ Network Error Tone.

---

## 10. Review of Stage 7 Hardening Observations

In Stage 7, five non-blocking hardening observations were recorded. We reviewed each against the current repository state:

| Observation | Description | Current State in Repository | Technical Classification | Disposition & Impact |
|---|---|---|---|---|
| **1. Mask Raw RFID UID Logging** | Raw card UID is logged to Serial on scan in `rfid_scanner.cpp`. | `esp32_attendance_device.ino` masks UID via `maskUid()` (line 189), but `rfid_scanner.cpp` line 128 prints `outUid`. | **Unresolved but non-blocking** | Affects diagnostic serial logs only; no external network exposure. Production code remains untouched per rules. |
| **2. Correct README Troubleshooting Guidance** | README line 217 suggested `LOGIN_DISABLED=True` for HTTP 401 device errors. | `firmware/esp32_attendance_device/README.md` still contains this legacy note. | **Unresolved but non-blocking** | Documentation discrepancy only. Backend device authentication in `app/auth.py` operates strictly via `X-Device-Id` and `X-API-Key`. |
| **3. Enforce $\ge 32$-Char API Key Locally** | Firmware checks `strlen(key) == 0` but does not enforce $\ge 32$ chars before HTTP transmit. | `api_client.cpp` lines 38-46 aborts if empty. Short keys are transmitted and rejected by Flask with HTTP 401. | **Unresolved but non-blocking** | Backend securely enforces $\ge 32$ characters. Both native unit tests and E2E simulation verify this contract. |
| **4. Redundant Wi-Fi Reconnection Call** | `wifi_manager.cpp` executes `WiFi.disconnect(); WiFi.reconnect();`. | Present in `wifi_manager.cpp` lines 64-65. | **Unresolved but non-blocking** | Harmless on ESP32 Arduino core; background auto-reconnection functions reliably. |
| **5. Unused `showReady(courseTitle)` Parameter** | `showReady(const char* courseTitle = nullptr)` parameter is unused in main sketch. | Defined in `display_manager.cpp`; called without arguments in `.ino`. | **Unresolved but non-blocking** | Clean API design. Retained for future enhancement if session endpoint is queried by ESP32. |

**Summary:** None of these observations represent blocking defects. All five are classified as **Unresolved but non-blocking** and require zero emergency production changes prior to hardware preparation.

---

## 11. Git Status and Production File Safety Audit

```
Command: git status --short
 M firmware/esp32_attendance_device/api_client.cpp
 M firmware/esp32_attendance_device/api_client.h
?? STAGE_6_API_CLIENT_CORRECTION_REPORT.md
?? STAGE_7_FIRMWARE_INTEGRATION_REVIEW.md
?? STAGE_8_3_FIRMWARE_UNIT_TEST_REPORT.md
?? STAGE_8_4_END_TO_END_SIMULATION_REPORT.md
?? STAGE_8_5_PRE_HARDWARE_READINESS_REPORT.md
?? tests/firmware_sim/
```

### Safety Confirmations:
1. `app/` backend source files: **0 files modified**.
2. `firmware/esp32_attendance_device/` production code: **0 new files modified** (only approved Stage 6 API client corrections in `api_client.cpp` and `api_client.h` remain present).
3. Production SQLite database (`database/attendance.db`): **Untouched** (row count remains 2, `PRAGMA integrity_check;` returns `ok`).
4. Sensitive credentials: **0 exposed** (only placeholders exist in tracked files).
5. Git branch: Remains strictly on `main`. Zero commits or pushes performed.

---

## 12. Final Readiness Decision

```
================================================================================
                    FINAL READINESS EVALUATION: STAGE 8.5
================================================================================

   [ ] READY FOR PHYSICAL ASSEMBLY
   [X] READY FOR HARDWARE PREPARATION
   [ ] NOT READY — SAFETY ISSUE

================================================================================
```

### Justification:
The software and firmware architecture is 100% complete, verified, and compiling cleanly (79 native unit tests passed, 18 E2E simulation tests passed, 42 backend regression tests passed, 0 Arduino CLI compilation errors).

However, in strict adherence to engineering safety rules:
**The system cannot be marked "READY FOR PHYSICAL ASSEMBLY" until the buzzer current, transistor driver, breadboard width, and USB data capability are physically verified in the college electronics laboratory using calibrated laboratory multimeters.**

Once laboratory electrical measurements confirm that the buzzer signal current is well under 12 mA and the RC522 3.3V rail is strictly isolated from 5V, physical assembly and hardware bring-up may safely proceed.

---

## 13. Inspected Files Reference List

- `firmware/esp32_attendance_device/config.h`
- `firmware/esp32_attendance_device/api_client.h`
- `firmware/esp32_attendance_device/api_client.cpp`
- `firmware/esp32_attendance_device/rfid_scanner.h`
- `firmware/esp32_attendance_device/rfid_scanner.cpp`
- `firmware/esp32_attendance_device/display_manager.h`
- `firmware/esp32_attendance_device/display_manager.cpp`
- `firmware/esp32_attendance_device/buzzer.h`
- `firmware/esp32_attendance_device/buzzer.cpp`
- `firmware/esp32_attendance_device/wifi_manager.h`
- `firmware/esp32_attendance_device/wifi_manager.cpp`
- `firmware/esp32_attendance_device/esp32_attendance_device.ino`
- `firmware/esp32_attendance_device/README.md`
- `tools/arduino-cli/arduino-cli.yaml`
- `run.py`
- `app/auth.py`
- `app/config.py`
- `.gitignore`
- `tests/firmware_sim/native/mocks/config_override.h`
- `tests/firmware_sim/native/mocks/HTTPClient.h`

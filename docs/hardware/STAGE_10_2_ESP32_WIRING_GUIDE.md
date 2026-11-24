# STAGE 10.2 — ESP32 HARDWARE WIRING GUIDE

**Project:** IoT-Based Smart Attendance Management System
**Document:** Physical Wiring, Pinout, and Assembly Manual
**Target Hardware:** ESP32-WROOM-32 (DevKit V1, 30-Pin Variant)
**Status:** Approved Provisional Wiring Plan
**Last Updated:** 2026-11-24

---

## 1. Safety Rules & Pre-Assembly Directives

> [!CAUTION]
> **READ BEFORE TOUCHING ANY PHYSICAL COMPONENTS:**
> 1. **ZERO LIVE WIRING:** Never insert, remove, or adjust jumper wires while the USB cable is plugged into the PC or power supply. Disconnect USB power before making any connection changes.
> 2. **RC522 3.3V POWER RESTRICTION:** The RC522 RFID reader is strictly a **3.3V device**. Connecting its `VCC` pin to the `VIN` or `5V` pin will destroy the MFRC522 IC instantly.
> 3. **BUZZER PROVISIONAL ISOLATION:** The active buzzer module must **NOT** be connected directly to ESP32 `GPIO 25` until bench-tested with a multimeter to confirm drive polarity (Active-HIGH vs Active-LOW) and control pin current ($< 5\text{ mA}$).
> 4. **OLED SOLDERING PREREQUISITE:** The 4-pin male header for the OLED display must be soldered cleanly to the OLED PCB prior to breadboard insertion. Do not attempt loose contact.

---

## 2. Component Inventory

The following physical parts have been identified and confirmed from user photographs:

| Item | Component Description | Interface / Form Factor | Power Requirements | Status |
| :---: | :--- | :--- | :--- | :---: |
| **MCU** | ESP32 DevKit V1 (30-pin, ESP-WROOM-32, CP2102/CH340) | Dual-row 15-pin DIP ($0.9''$ row spacing) | 5V via Micro-USB; 3.3V internal LDO | **CONFIRMED** |
| **RFID** | NXP MFRC522 13.56 MHz RFID Reader Board | 8-pin male header (SPI) | **3.3V ONLY** ($\le 26\text{ mA}$) | **CONFIRMED** |
| **TAGS** | MIFARE Classic 1K Contactless Cards & Keyfobs | ISO 14443A (13.56 MHz passive) | Passive (RF field powered) | **CONFIRMED** |
| **OLED** | 0.96-inch Monochrome OLED (`RG0.96 IC V2.0`, SSD1306) | 4-pin header (I2C: GND, VDD, SCK, SDA) | **3.3V Regulated** ($\le 25\text{ mA}$) | **CONFIRMED** |
| **BUZZER** | Active Buzzer Module (`adiy 2608`, Made in India) | 3-pin header (`VCC`, `I/O`, `GND`) | 3.3V or 5V (Pending Bench Check) | **PROVISIONAL** |
| **CAP** | Bulk Electrolytic / Tantalum Capacitor | Polarized Radial Lead ($47\,\mu\text{F} - 100\,\mu\text{F}$, $\ge 10\text{V}$) | Passive filtering | **RECOMMENDED** |
| **BASE** | 830-Point Solderless Breadboard | Standard dual power rails + center trough | N/A | **CONFIRMED** |
| **WIRES** | Male-to-Male and Male-to-Female Jumper Wires | 24–26 AWG insulated jumpers | N/A | **CONFIRMED** |
| **CABLE** | Micro-USB to USB-A Data/Power Cable | Micro-B male to USB-A male | 5.0V VBUS ($\ge 500\text{ mA}$) | **CONFIRMED** |

---

## 3. Comprehensive Connection Tables

### 3.1 ESP32 DevKit V1 to RC522 RFID Reader (SPI Bus)

The RC522 module uses the hardware VSPI bus for high-speed data transfer.
*Critical Revision:* Slave Select (SS/SDA) is mapped to **GPIO 27** (D27) to avoid GPIO5 boot-strapping conflicts and power-up clock glitches.

| RC522 Pin | ESP32 Pin Mark | ESP32 GPIO | Signal Function | Wire Color (Rec.) | Status |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **SDA / NSS** | **D27** | GPIO 27 | SPI Slave Select (Active LOW) | **Yellow** | **CONFIRMED (Stage 10.2)** |
| **SCK** | **D18** | GPIO 18 | SPI Bus Clock | **Green** | **CONFIRMED** |
| **MOSI** | **D23** | GPIO 23 | SPI Master-Out-Slave-In | **Blue** | **CONFIRMED** |
| **MISO** | **D19** | GPIO 19 | SPI Master-In-Slave-Out | **Violet** | **CONFIRMED** |
| **RST** | **D4** | GPIO 4 | Hardware Reset | **White** | **CONFIRMED** |
| **3.3V / VCC** | **3V3** | 3.3V Rail | 3.3V DC Power Input | **Red** | **CRITICAL: 3.3V ONLY** |
| **GND** | **GND** | GND Rail | System Ground Reference | **Black** | **CONFIRMED** |
| **IRQ** | *N/C* | *None* | Interrupt Output (Unconnected) | *None* | **LEAVE DISCONNECTED** |

> [!WARNING]
> Under no circumstances connect the RC522 3.3V pin to VIN (5V). The MFRC522 transceiver IC will be permanently destroyed.

---

### 3.2 ESP32 DevKit V1 to SSD1306 0.96" OLED Display (I2C Bus)

The OLED module communicates over the hardware I2C bus (`Wire.h`) at default address `0x3C`.
*Assembly Prerequisite:* Solder the supplied 4-pin male header to pads 1–4 before breadboard insertion.

| OLED Pin Number & Silk | ESP32 Pin Mark | ESP32 GPIO | Signal Function | Wire Color (Rec.) | Status |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **Pin 1: GND** | **GND** | GND Rail | Power & Signal Ground | **Black** | **CONFIRMED** |
| **Pin 2: VDD** | **3V3** | 3.3V Rail | 3.3V Regulated Power | **Red** | **CONFIRMED (3.3V)** |
| **Pin 3: SCK** | **D22** | GPIO 22 | I2C Serial Clock (SCL) | **Orange** | **CONFIRMED** |
| **Pin 4: SDA** | **D21** | GPIO 21 | I2C Serial Data (SDA) | **Brown** | **CONFIRMED** |

*Notes on OLED:*
1. The silk screen on the display board explicitly labels Pin 1 as `GND` and Pin 2 as `VDD`. Always verify this numbering against your board before powering up.
2. Connecting `VDD` to the 3.3V rail ensures that the onboard $10\text{ k}\Omega$ I2C pull-up resistors pull `SCK` and `SDA` strictly to 3.3V, matching the ESP32 input thresholds.

---

### 3.3 ESP32 DevKit V1 to Active Buzzer Module (`adiy 2608`)

The active buzzer module includes an onboard switching circuit and internal audio oscillator.
*Safety Status:* **PROVISIONAL.** Pending physical bench testing of logic polarity and input current.

| Buzzer Pin Silk | ESP32 Pin Mark | ESP32 GPIO | Function / Connection | Status |
| :---: | :---: | :---: | :---: | :---: |
| **I/O** | **D25** | GPIO 25 | Control Signal (Output) | **PROVISIONAL (Bench Check Required)** |
| **VCC** | **3V3** or **VIN** | Power Rail | Module Power ($\le 40\text{ mA}$) | **PROVISIONAL (Bench Check Required)** |
| **GND** | **GND** | GND Rail | Ground Reference | **CONFIRMED** |

*Buzzer Bench Verification Rules:*
1. Do not connect the `I/O` pin directly to `GPIO 25` until you have applied 3.3V to `VCC` and GND to `GND` on the bench.
2. If touching `I/O` to 3.3V triggers the beep, the module is **Active-HIGH** (`#define BUZZER_ACTIVE_HIGH true` in `config.h`).
3. If touching `I/O` to GND triggers the beep, the module is **Active-LOW** (`#define BUZZER_ACTIVE_HIGH false` in `config.h`).
4. Measure the DC current flowing into `I/O` using a multimeter in current mode: it must be well below **12 mA** (typically $< 3\text{ mA}$ with the onboard $1\text{ k}\Omega$ resistor).

---

## 4. Power & Ground Distribution Architecture

```
  +-----------------------------------------------------------------------+
  |  PC USB Host (Rear USB 3.0 / 2.0 Port >= 500mA)                       |
  +-----------------------------------+-----------------------------------+
                                      | (Micro-USB Cable)
                                      v
  +-----------------------------------------------------------------------+
  |  ESP32 DevKit V1                                                      |
  |  [ Onboard Linear Regulator (AMS1117-3.3) ]                           |
  |    5.0V USB VBUS -----------> Regulated 3.30V DC Output (Max 500mA)    |
  +-----------------+-----------------------------------+-----------------+
                    |                                   |
         3.3V Rail  |                        GND Rail   |
                    v                                   v
  +-----------------------------------+   +-------------------------------+
  | Breadboard 3.3V Distribution Rail |   | Breadboard Common Ground Rail |
  +--------+--------+--------+--------+   +-------+-------+-------+-------+
           |        |        |                    |       |       |
           |        |        +--> [Bulk Cap] <----+       |       |
           |        |             (47uF-100uF)            |       |
           |        +--> OLED VDD                         +--> OLED GND
           |             (Pin 2)                               (Pin 1)
           +-----------> RC522 3.3V                       +--> RC522 GND
                         (3.3V ONLY)
```

### 4.1 Common Ground Requirement
A single, continuous, uninterrupted Ground rail must be established across the breadboard. All four components (ESP32 `GND`, RC522 `GND`, OLED `GND`, Buzzer `GND`) must connect directly to this rail. Floating or disconnected ground wires will cause SPI bus timeouts, I2C NACK failures, and erratic voltage offsets.

### 4.2 Bulk Decoupling Capacitor Placement
- **Component:** Electrolytic or tantalum capacitor, $47\,\mu\text{F}$ to $100\,\mu\text{F}$, rated for $10\text{V}$ or $16\text{V}$.
- **Placement:** Insert directly across the breadboard 3.3V rail and GND rail as close as possible to the ESP32 `3V3` and `GND` pins.
- **Polarity Warning:**
  - Electrolytic capacitors are polarized.
  - The side with the **white or gray stripe and minus signs (`-`)** must connect to the **GND rail**.
  - The longer lead (positive) must connect to the **3.3V rail**.
  - Connecting a polarized capacitor backwards can cause it to overheat and vent.

### 4.3 Brownout Prevention & Cable Selection
- **The Problem:** When the ESP32 Wi-Fi radio initiates an RF transmission burst (+19.5 dBm in 802.11b mode), instantaneous current spikes can reach **$380\text{ mA} - 450\text{ mA}$** for tens of microseconds.
- **The Cable Risk:** Thin or long consumer Micro-USB cables have high loop resistance ($> 1\,\Omega$), which causes a voltage drop ($\Delta V = I \times R \approx 0.45\text{V}$). This starves the onboard AMS1117 LDO regulator of required dropout headroom ($1.2\text{V}$), causing the 3.3V rail to dip below $2.8\text{V}$ and triggering a brownout reset loop.
- **The Solution:**
  1. Always plug the USB cable into a **direct motherboard USB 3.0/2.0 rear port** on your PC. Never use unpowered USB hubs or front-panel case extension cables.
  2. The $47\,\mu\text{F} - 100\,\mu\text{F}$ bulk capacitor acts as an immediate local charge reservoir during microsecond Wi-Fi bursts.

---

## 5. Breadboard Layout & Physical Orientation

### 5.1 ESP32 DevKit V1 Physical Placement
- Position the ESP32 DevKit V1 straddling the **center dividing trough** of the 830-point solderless breadboard.
- Orient the board with the Micro-USB connector facing the outer edge of the breadboard for easy cable access.
- Ensure the board is pressed firmly into the tie points so all 30 pins make solid contact.

### 5.2 Header Pin Locations (30-Pin ESP32 DevKit V1)

```
                       +-------------------+
                       |    [Micro-USB]    |
                       +-------------------+
             EN [ 1 ]                      [ 30 ] D23 (GPIO 23) -> RC522 MOSI
             VP [ 2 ]                      [ 29 ] D22 (GPIO 22) -> OLED SCK (SCL)
             VN [ 3 ]                      [ 28 ] TX0 (GPIO 1)
            D34 [ 4 ]                      [ 27 ] RX0 (GPIO 3)
            D35 [ 5 ]                      [ 26 ] D21 (GPIO 21) -> OLED SDA
            D32 [ 6 ]                      [ 25 ] D19 (GPIO 19) -> RC522 MISO
            D33 [ 7 ]                      [ 24 ] D18 (GPIO 18) -> RC522 SCK
Buzzer I/O->D25 [ 8 ]                      [ 23 ] D5  (GPIO 5 - UNUSED)
            D26 [ 9 ]                      [ 22 ] TX2 (GPIO 17)
RC522 SS -> D27 [ 10]                      [ 21 ] RX2 (GPIO 16)
            D14 [ 11]                      [ 20 ] D4  (GPIO 4)  -> RC522 RST
            D12 [ 12]                      [ 19 ] D2  (GPIO 2)
            D13 [ 13]                      [ 18 ] D15 (GPIO 15)
     GND -> GND [ 14]                      [ 17 ] GND -> Common Ground
            VIN [ 15]                      [ 16 ] 3V3 -> 3.3V Power Rail
                       +-------------------+
                       |   ESP32-WROOM-32  |
                       |    RF Shielding   |
                       +-------------------+
```

---

## 6. Step-by-Step Safe Assembly Order

Assemble the circuit strictly in the following sequential order with all power disconnected:

### Step 1: Component Preparation (Workbench)
1. **Solder OLED Header:** Solder the 4-pin male header to the OLED PCB (`GND`, `VDD`, `SCK`, `SDA`). Inspect joints under magnification for bridges.
2. **Inspect Buzzer:** Examine the buzzer module. Check if an external driver is needed or if bench test passes.

### Step 2: Breadboard Placement (Zero Power)
1. Mount the ESP32 DevKit V1 across the center trough of the breadboard.
2. Mount the soldered OLED display onto an open area of the breadboard.
3. If using breadboard mounting for RC522, position it with antenna coil facing away from metal objects.

### Step 3: Establish Power & Ground Rails
1. Run a jumper from ESP32 **GND** (pin 14 or 17) to the breadboard **blue GND rail**.
2. Run a jumper from ESP32 **3V3** (pin 16) to the breadboard **red 3.3V rail**.
3. Insert the $47\,\mu\text{F} - 100\,\mu\text{F}$ capacitor across the red (3.3V) and blue (GND) rails. Observe polarity!

### Step 4: Wire RC522 RFID Reader
1. Wire RC522 **3.3V** pin $\rightarrow$ Breadboard **red 3.3V rail**. *(DO NOT USE VIN).*
2. Wire RC522 **GND** pin $\rightarrow$ Breadboard **blue GND rail**.
3. Wire RC522 **RST** pin $\rightarrow$ ESP32 **D4** (GPIO 4).
4. Wire RC522 **MISO** pin $\rightarrow$ ESP32 **D19** (GPIO 19).
5. Wire RC522 **SCK** pin $\rightarrow$ ESP32 **D18** (GPIO 18).
6. Wire RC522 **MOSI** pin $\rightarrow$ ESP32 **D23** (GPIO 23).
7. Wire RC522 **SDA / SS** pin $\rightarrow$ ESP32 **D27** (GPIO 27). *(CONFIRMED REVISION).*
8. Leave RC522 **IRQ** unconnected.

### Step 5: Wire SSD1306 OLED Display
1. Wire OLED **Pin 1 (GND)** $\rightarrow$ Breadboard **blue GND rail**.
2. Wire OLED **Pin 2 (VDD)** $\rightarrow$ Breadboard **red 3.3V rail**.
3. Wire OLED **Pin 3 (SCK)** $\rightarrow$ ESP32 **D22** (GPIO 22).
4. Wire OLED **Pin 4 (SDA)** $\rightarrow$ ESP32 **D21** (GPIO 21).

### Step 6: Wire Buzzer Module (Subject to Bench Clearance)
1. Wire Buzzer **GND** $\rightarrow$ Breadboard **blue GND rail**.
2. Wire Buzzer **VCC** $\rightarrow$ Breadboard **red 3.3V rail** (or 5V if verified for 5V).
3. Wire Buzzer **I/O** $\rightarrow$ ESP32 **D25** (GPIO 25).

---

## 7. Pre-Power Inspection Checklist

Before plugging the USB cable into your computer, complete this mandatory verification checklist:

```
[ ] 1. UNPOWERED CONTINUITY CHECK (MULTIMETER IN OHMS / DIODE MODE)
    - Place black probe on GND rail and red probe on 3.3V rail.
    - Result: Resistance must show charging behavior rising into kilo-ohms / mega-ohms.
    - Result MUST NOT be 0 ohms (short circuit). If shorted, DO NOT APPLY POWER.

[ ] 2. RC522 VCC VOLTAGE VERIFICATION
    - Visually trace the wire from RC522 pin marked "3.3V".
    - Confirm it terminates strictly at the breadboard 3.3V rail / ESP32 pin 3V3.
    - Confirm it does NOT touch the VIN or 5V pin.

[ ] 3. CAPACITOR POLARITY CHECK
    - Check the negative stripe on the capacitor body.
    - Confirm the negative lead is inserted into the GND (blue) rail.

[ ] 4. OLED PIN 1 & PIN 2 CHECK
    - Verify OLED Pin 1 (GND) connects to GND rail.
    - Verify OLED Pin 2 (VDD) connects to 3.3V rail.
    - Confirm Pin 3 (SCK) and Pin 4 (SDA) are not swapped.

[ ] 5. INITIAL VOLTAGE CHECK (FIRST POWER-ON PROTOCOL)
    - Keep peripheral VCC lines disconnected from the breadboard rail initially.
    - Plug USB cable into ESP32 DevKit V1.
    - Measure DC voltage between ESP32 pin 3V3 and pin GND using multimeter.
    - Verified Reading: Must be 3.30V +/- 0.10V (3.20V to 3.40V).
    - If voltage is normal, disconnect USB power, plug peripheral VCC jumpers back into the 3.3V rail, and proceed.
```

---

## 8. Summary of Confirmed vs Provisional Assignments

| Peripheral Connection | Assigned ESP32 Pin | Status | Prerequisite for Live Commissioning |
| :--- | :---: | :---: | :--- |
| **RC522 SPI Bus (SCK, MOSI, MISO, RST)** | GPIO 18, 23, 19, 4 | **CONFIRMED** | Trace wires against DevKit labels. |
| **RC522 Chip Select (SDA / SS)** | **GPIO 27** | **CONFIRMED** | Replaces GPIO5. Firmware updated in Stage 10.2. |
| **RC522 Power (3.3V, GND)** | 3V3, GND | **CONFIRMED** | Strictly 3.3V power only. |
| **SSD1306 OLED (SCK, SDA, VDD, GND)** | GPIO 22, 21, 3V3, GND | **CONFIRMED** | 4-pin male header soldered to PCB. |
| **Active Buzzer (I/O, VCC, GND)** | GPIO 25, 3V3/5V, GND | **PROVISIONAL** | Multimeter bench check of polarity & current. |

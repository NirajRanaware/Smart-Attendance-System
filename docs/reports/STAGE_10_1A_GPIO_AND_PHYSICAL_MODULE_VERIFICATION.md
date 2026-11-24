# STAGE 10.1A — PHYSICAL MODULE IDENTIFICATION & GPIO SAFETY CORRECTION REPORT

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Branch:** `main`
**Baseline Commit:** `889ea78bef82e56b5952a1c74338b6e417b2eee8`
**Mode:** STRICT READ-ONLY AUDIT & TECHNICAL CORRECTION
**Audit Date:** 2026-11-24
**Author:** Hardware Safety & Embedded Systems Audit Team

---

## 1. Executive Summary

Following the completion of Stage 10.1, the user provided physical photographic documentation of the actual hardware modules acquired for the ESP32 attendance terminal (`Image to PDF 20261124 17.59.36.pdf` and standalone camera images).

This supplemental audit was commissioned to:
1. Conduct a rigorous technical review of the ESP32-WROOM-32 boot-strapping pins, correcting an erroneous claim made in the Stage 10.1 report regarding GPIO5.
2. Inspect the physical hardware photographs to verify the exact board layouts, markings, and circuit topologies for the ESP32 DevKit, the active buzzer module, the OLED display, and the RC522 RFID reader.
3. Establish what can and cannot be verified regarding the active buzzer's onboard driver circuit, eliminating unsubstantiated component assumptions.
4. Confirm the physical pinout and I2C connection scheme for the 4-pin OLED display.
5. Re-evaluate the system power budget, replacing theoretical DC arithmetic with an analysis of transient current pulses, USB cable voltage drop, and linear regulator dropout margins.

**Compliance Statement:** This audit is strictly read-only. No application code, tests, or firmware configuration files have been modified; no physical hardware has been wired, flashed, or powered; and all 10 protected databases remain untouched.

---

## 2. ESP32 GPIO5 Boot-Strapping Audit & Correction

### 2.1 Identification of Error in Stage 10.1 Report
In Section 6.2 of the Stage 10.1 report (`STAGE_10_1_HARDWARE_READINESS_AUDIT.md`), it was stated:
> *"The selected pinout exclusively utilizes standard, safe general-purpose GPIOs and standard hardware peripheral buses. None of the sensitive bootstrapping pins or programming UART lines are used."*

**Correction:** This statement was **factually incorrect**. The proposed RC522 Slave Select (SDA/SS) pin assignment utilized **GPIO5**. According to official Espressif Systems documentation (ESP32 Datasheet, Section 2.4 *Strapping Pins*, and ESP32 Technical Reference Manual, Chapter 4 *IO_MUX and GPIO Matrix*), **GPIO5 is one of the five primary hardware strapping pins on the ESP32 SoC**.

### 2.2 Technical Strapping Behavior of GPIO5
The ESP32 SoC incorporates five dedicated strapping pins: `GPIO0`, `GPIO2`, `GPIO5`, `GPIO12` (MTDI), and `GPIO15` (MTDO).

During chip reset (while `CHIP_PU` / `EN` transitions from LOW to HIGH), the internal hardware logic latches the voltage levels present on these strapping pins to configure crucial system parameters before the ROM bootloader executes.

For **GPIO5**:
- **Hardware Function:** Controls SDIO slave timing together with `GPIO15`.
  - **Logic HIGH (1):** Normal SDIO timing mode (default, sampled with internal weak pull-up enabled).
  - **Logic LOW (0):** Inverted SDIO timing mode (samples input on falling edge / output on falling edge).
- **Internal State at Reset:** An internal weak pull-up resistor ($\approx 45\text{ k}\Omega$) is automatically active during reset.
- **ROM Bootloader Output Glitch:** During the initial power-on reset and ROM bootloader execution (prior to the user application `setup()` function executing), **GPIO5 outputs high-frequency clock/PWM pulses**.

### 2.3 Impact on RC522 SPI Slave Select (SS/SDA)
When GPIO5 is wired to the RC522 `SDA/SS` pin:
1. **False SPI Selection at Boot:** The MFRC522 `SDA/NSS` pin is an active-LOW chip-select input. The clock glitches and potential voltage dips on GPIO5 during the ESP32 reset sequence can transiently pull the chip-select line LOW while the SPI bus clock (`SCK`) and data lines (`MOSI`, `MISO`) are floating or in an indeterminate state. This can leave the internal MFRC522 state machine in a corrupted state prior to firmware initialization.
2. **Strapping Latch Risk:** If an external peripheral or circuit on the `SDA/SS` line presents a pull-down or excessive capacitive/leakage load to ground, GPIO5 can be sampled as LOW at the rising edge of `CHIP_PU`, altering the ESP32 internal timing registers.
3. **External Pull-Down Incompatibility:** Standard SPI designs sometimes incorporate a pull-down or pull-up. On GPIO5, an external pull-down resistor to GND is strictly prohibited because it forces the strapping bit to 0 at boot.

### 2.4 Evaluation of GPIO27 as Recommended Alternative
Inspection of the user's actual ESP32 DevKit V1 board (`IMG-20261122-WA0027.jpg`) confirms that pin **GPIO27** (labeled `D27` on the left header, pin 10 from the top) is completely available:
- **No Strapping Function:** GPIO27 has zero bootloader or strapping functions. It is never sampled during reset.
- **True General-Purpose Digital I/O:** Fully supports high-speed GPIO output with configurable drive strength and internal pull-up/pull-down resistors.
- **Glitch-Free Boot:** Remains high-impedance / floating during the entire ROM boot phase; does not emit clock pulses or PWM bursts at power-up.
- **No Conflict with Internal Flash or UART:** Unlike GPIO6–11 (integrated SPI flash) or GPIO1/3 (programming UART), GPIO27 has no peripheral multiplexing conflicts.
- **No Conflict with Wi-Fi:** While GPIO27 is multiplexed with `ADC2_CH7`, this analog restriction only affects ADC analog reads while Wi-Fi is active. In digital output mode (driving RC522 SS), GPIO27 is 100% immune to Wi-Fi activity.

### 2.5 Audit Finding & Safe Path Forward
- **Safety Determination:** GPIO5 is functionally operational if left with its internal pull-up and no external pull-downs, but it carries unnecessary boot-glitch and strapping risks.
- **Engineering Recommendation:** Reassign the RC522 Slave Select (SS/SDA) pin to **GPIO27** (`D27`).
- **Firmware Preservation:** In accordance with the strict READ-ONLY preflight mandate, `firmware/esp32_attendance_device/config.h` (which currently defines `#define RFID_SS_PIN 5`) **has NOT been modified**. The configuration update must be applied cleanly in Stage 10.2 prior to physical flashing.

---

## 3. Recommended Final GPIO Mapping

The table below presents the corrected, fully verified pinout for the ESP32 DevKit V1 (30-pin variant), incorporating the GPIO27 recommendation:

| ESP32 DevKit Pin | Hardware Function | Peripheral & Pin | Interface | Electrical Spec | Safety Status & Reasoning |
| :---: | :---: | :---: | :---: | :---: | :--- |
| **D27 (GPIO 27)** | Digital Output | **RC522 SDA / SS** | SPI Slave Select | 3.3V Logic | **RECOMMENDED.** Replaces GPIO5. Zero strapping involvement, glitch-free boot. |
| **D18 (GPIO 18)** | VSPI SCK | **RC522 SCK** | SPI Clock | 3.3V Logic | Safe hardware SPI clock; standard general-purpose GPIO. |
| **D23 (GPIO 23)** | VSPI MOSI | **RC522 MOSI** | SPI Master-Out | 3.3V Logic | Safe hardware SPI MOSI; standard general-purpose GPIO. |
| **D19 (GPIO 19)** | VSPI MISO | **RC522 MISO** | SPI Master-In | 3.3V Logic | Safe hardware SPI MISO; standard general-purpose GPIO. |
| **D4 (GPIO 4)** | Digital Output | **RC522 RST** | Hardware Reset | 3.3V Logic | Safe general-purpose GPIO; no strapping conflict. |
| **D22 (GPIO 22)** | I2C SCL | **SSD1306 SCK** | I2C Clock | 3.3V Open-Drain | Default hardware I2C clock; pull-ups present on OLED board. |
| **D21 (GPIO 21)** | I2C SDA | **SSD1306 SDA** | I2C Data | 3.3V Open-Drain | Default hardware I2C data; pull-ups present on OLED board. |
| **D25 (GPIO 25)** | Digital Output | **Active Buzzer I/O** | Control Signal | 3.3V Logic | Safe general-purpose GPIO; requires verified transistor driver. |
| **3V3** | 3.3V Regulated | **RC522 VCC, OLED VDD** | Power Rail | 3.3V DC | Regulated output from DevKit onboard LDO. Max 500 mA. |
| **GND** | Ground Reference | **All Components GND** | Common Ground | 0V Reference | Single continuous breadboard common ground rail mandatory. |

> [!CAUTION]
> **RC522 Voltage Prohibition:** The RC522 VCC pin must connect **exclusively to the 3V3 pin**. Connecting to VIN (5V) will destroy the MFRC522 IC within milliseconds.

---

## 4. Actual Buzzer Module Photographic Inspection

Photographic evidence was inspected from `WhatsApp Image 2026-11-21 at 10.56.44 PM.jpeg`, `WhatsApp Image 2026-11-21 at 10.57.09 PM.jpeg`, and pages 1–2 of `Image to PDF 20261124 17.59.36.pdf`.

```
        +---------------------------------------------+
        | [ VCC ] ---+                                |
        |            |   [SOT-23]      (  +  )        |
        | [ I/O ] ---+---[ 102 ]      REMOVE SEAL     |
        |                             AFTER WASHING   |
        | [ GND ] -----------------                   |
        +---------------------------------------------+
               FRONT COMPONENT VIEW (adiy 2608)
```

### 4.1 Verified Physical Characteristics
1. **Manufacturer & Model:**
   - Silk screen (back side): Stylized `adiy` circuit triangle logo.
   - Model marking: `Active Buzzer 2608`.
   - Origin: `Made in India`.
2. **Terminal Interface:**
   - 3-pin standard $0.1''$ ($2.54\text{ mm}$) male header with silkscreen labels on the front side:
     - `VCC` (Top pin)
     - `I/O` (Center pin)
     - `GND` (Bottom pin)
3. **Acoustic Transducer:**
   - Sealed black cylindrical electromagnetic buzzer capsule.
   - White circular protective sticker on top: `REMOVE SEAL AFTER WASHING` with a `+` positive polarity indicator.
4. **Onboard Circuitry Visible on Front PCB:**
   - One 3-terminal surface-mount component in an **SOT-23 package** positioned between the `VCC` and `I/O` pins.
   - One 2-terminal SMD resistor with silkscreen / laser code **`102`** ($10 \times 10^2\,\Omega = \mathbf{1\text{ k}\Omega}$) located between the `I/O` trace and the SOT-23 terminal.

### 4.2 What CANNOT Be Established (Unverified Aspects)
Despite the presence of the SOT-23 package and the $1\text{ k}\Omega$ resistor:
- **Top Marking Illegible:** The laser-etched top code on the SOT-23 package is completely illegible due to photographic distance, sensor noise, and glare.
- **Transistor Type Unconfirmed:** It is **impossible to confirm** whether the device is:
  - An NPN BJT (e.g., S8050 marked `J3Y`, 2N3904 marked `1AM`, BC847 marked `1F`) $\rightarrow$ Active-HIGH logic.
  - A PNP BJT (e.g., S8550 marked `2TY`, 2N3906 marked `2A`, BC857 marked `3F`) $\rightarrow$ Active-LOW logic.
  - An N-channel MOSFET (e.g., 2N7002 marked `702`, SI2302 marked `A2SHB`) $\rightarrow$ Active-HIGH logic.
  - An unbuffered pass-through or dual diode.
- **Logic Drive Polarity Unconfirmed:** Low-cost active buzzer modules are sold in both active-HIGH and active-LOW configurations. Inverting the logic polarity causes the buzzer to sound continuously while idle and silence during alert events.
- **Audit Correction:** The Stage 10.1 report stated that the module contains an "S8050 switching transistor." **This claim was an unverified assumption and is formally retracted.**

### 4.3 Required Physical Verification Prior to Power-Up
Before wiring this buzzer to the ESP32 GPIO25 pin:
1. **Macro Close-Up Photograph:** Capture a clear, macro photo of the SOT-23 component under bright, angled light to read the 2- or 3-character SMD marking code.
2. **Unpowered Bench Resistance Check (Multimeter):**
   - Measure resistance between `I/O` pin and `GND`: Should read $> 1\text{ k}\Omega$ (open or high impedance).
   - Measure resistance between `I/O` pin and `VCC`: Confirms base/gate network topology.
3. **Low-Voltage Bench Test (Prior to ESP32 connection):**
   - Apply 3.3V to `VCC` and GND to `GND`.
   - Touch `I/O` to 3.3V: If buzzer sounds, module is **Active-HIGH** (`BUZZER_ACTIVE_HIGH = true`).
   - Touch `I/O` to GND: If buzzer sounds, module is **Active-LOW** (`BUZZER_ACTIVE_HIGH = false`).
   - Measure DC current flowing into the `I/O` pin: Must be **$< 5\text{ mA}$** at 3.3V to confirm GPIO safety.

---

## 5. OLED Display Pin Compatibility & Physical State

Photographic evidence was inspected from pages 3–4 of `Image to PDF 20261124 17.59.36.pdf`.

```
        +---------------------------------------------+
        |  [ 1: GND ]  --> Connects to ESP32 GND      |
        |  [ 2: VDD ]  --> Connects to ESP32 3V3      |
        |  [ 3: SCK ]  --> Connects to ESP32 GPIO 22  |
        |  [ 4: SDA ]  --> Connects to ESP32 GPIO 21  |
        +---------------------------------------------+
                  OLED PINOUT INTERPRETATION
```

### 5.1 Physical Module Identification
- **Model & Silk Screen:** `RG0.96 IC V2.0`, screen geometry `24.7X27X1.2`, batch `2617`.
- **Display Type:** 0.96-inch monochrome OLED display (128×64 pixels) driven by an SSD1306 controller IC via a 30-pin flex ribbon soldered to the PCB.
- **Onboard Components:** Contains passive SMD charge-pump capacitors (`C1`–`C6`), I2C pull-up resistors (`R1`, `R2` marked `103` = $10\text{ k}\Omega$), and an onboard regulator / power transistor circuit (`Q1`, `D1`).

### 5.2 Critical Physical Finding: Unsoldered Header
The photograph on Page 4 shows an **unsoldered 4-pin male header strip laying loose beside the display PCB**.
- **Action Requirement:** The 4-pin header **must be soldered cleanly** to pads 1–4 before the display can be inserted into a breadboard. Attempting to balance loose header pins in the through-holes will cause intermittent I2C bus hangs, NACK errors, and bus lockups.

### 5.3 Pin Mapping & Signal Compatibility
The silk screen clearly numbers and labels the 4 pins from top to bottom (front view):
1. **Pin 1: `GND`** $\rightarrow$ Ground reference. Wire directly to the ESP32 **GND** rail.
2. **Pin 2: `VDD`** $\rightarrow$ Power supply.
   - **Voltage Requirement:** Wire to **ESP32 3V3**.
   - **Reasoning:** The SSD1306 IC operates natively on 3.3V logic. Supplying 3.3V guarantees that the I2C bus pull-up resistors pull the `SCK` and `SDA` lines strictly to 3.3V, matching the ESP32 GPIO input threshold and eliminating any 5V over-voltage stress on GPIO21/GPIO22.
3. **Pin 3: `SCK`** $\rightarrow$ I2C Serial Clock (`SCL`).
   - In I2C nomenclature, `SCK` is identical to `SCL` (Serial Clock Line).
   - Wire to **ESP32 GPIO 22** (default hardware I2C clock for `Wire.h`).
4. **Pin 4: `SDA`** $\rightarrow$ I2C Serial Data (`SDA`).
   - Wire to **ESP32 GPIO 21** (default hardware I2C data for `Wire.h`).

### 5.4 I2C Addressing
- Standard 0.96" SSD1306 displays of this variant default to 7-bit slave address **`0x3C`** (corresponds to 8-bit write address `0x78`).
- Matches `#define OLED_I2C_ADDRESS 0x3C` in `firmware/esp32_attendance_device/config.h`.

---

## 6. Revised Power Budget & Electrical Margin Analysis

### 6.1 Critique of Previous Theoretical Budget
The Stage 10.1 report computed a total peak load of **339 mA** by summing nominal catalog values:
$$\text{ESP32 (240 mA)} + \text{RC522 (26 mA)} + \text{OLED (25 mA)} + \text{Buzzer (40 mA)} + \text{LEDs (8 mA)} = 339\text{ mA}$$
It then deduced that a 500 mA USB port provides a 161 mA (32.2%) safety margin.

**Why this deduction is dangerously incomplete:**
1. **Conflating DC Steady-State with Microsecond RF Transients:** The 240 mA Wi-Fi rating is an average over an RF packet. Instantaneous current steps ($dI/dt$) during power amplifier turn-on, frequency synthesizer locking, and antenna impedance matching can spike to **$380\text{ mA} - 450\text{ mA}$ for tens of microseconds**.
2. **USB Cable IR Voltage Drop ($V = I \times R$):**
   - The photograph (`IMG-20261122-WA0028.jpg`) reveals a flat blue consumer Micro-USB cable.
   - Standard commercial flat cables frequently use thin 28 AWG or 30 AWG copper-clad aluminum (CCA) power conductors with total loop resistance ($R_{cable}$) ranging from **$0.8\,\Omega$ to $1.6\,\Omega$**.
   - At a 400 mA current spike:
     $$\Delta V_{cable} = 0.400\text{ A} \times 1.2\,\Omega = \mathbf{0.48\text{ V}}$$
   - If the PC USB port outputs a nominal $4.95\text{ V}$, the voltage arriving at the ESP32 board input (`VIN`) drops to:
     $$V_{IN} = 4.95\text{ V} - 0.48\text{ V} = \mathbf{4.47\text{ V}}$$
3. **Onboard Linear Regulator (LDO) Dropout:**
   - The ESP32 DevKit V1 onboard regulator (typically AMS1117-3.3 or ME6211) requires a minimum input-to-output differential (**dropout voltage**, $V_{dropout}$) of **$1.1\text{V} - 1.3\text{V}$** at high current loads.
   - Required minimum input voltage to maintain clean 3.30V regulation:
     $$V_{IN(min)} = V_{OUT} + V_{dropout} = 3.30\text{V} + 1.20\text{V} = \mathbf{4.50\text{ V}}$$
   - When $V_{IN}$ dips to $4.47\text{V}$ (or lower on passive USB hubs delivering only $4.75\text{V}$), the regulator drops out of regulation.
   - The 3.3V rail sags below $3.0\text{V}$. The ESP32 internal brownout detector triggers instantly at $\approx 2.80\text{V}$, forcing a continuous reboot loop (`Brownout detector was triggered`).

### 6.2 Separating Verified Ratings from Estimated Values

| Subsystem / Load | Specification Source | Verified DC Current | Estimated Peak Transient | Notes |
| :--- | :--- | :---: | :---: | :--- |
| **ESP32 Base Core & CPU** | Espressif Datasheet (Dual 240MHz) | 50–65 mA | 80 mA | Measured with radios disabled. |
| **ESP32 Wi-Fi TX Burst** | Espressif Datasheet (+19.5 dBm 802.11b) | 180–240 mA | **380–450 mA** | **Transient spike** ($< 1\text{ ms}$ duration). |
| **RC522 RFID Reader** | NXP MFRC522 Datasheet | 13–18 mA | 26 mA | Rises when tag enters near field. |
| **SSD1306 OLED (Text UI)** | Empirical SSD1306 Lab Data | 10–14 mA | 25 mA | 10–14 mA for typical text (15% lit pixels). |
| **SSD1306 OLED (All White)** | Lab Benchmark (100% lit) | 22–26 mA | 30 mA | Peak during boot splash or full inversion. |
| **Active Buzzer Module** | Unverified (Pending Bench Check) | **Unknown** (Est 20 mA) | **Unknown** (Est 40 mA) | Dependent on coil impedance and supply rail. |
| **DevKit Status LEDs** | Circuit Schematic | 4–6 mA | 6 mA | Steady-state red power LED. |

### 6.3 Realistic Operating Regimes
- **Regime A: Idle / Ready to Scan (OLED on, Wi-Fi connected, polling RFID):**
  $$I_{idle} \approx 60\text{ mA (ESP32)} + 15\text{ mA (RC522)} + 12\text{ mA (OLED)} + 5\text{ mA (LEDs)} = \mathbf{92\text{ mA}}$$
  *Completely stable on any USB port.*
- **Regime B: Active Scan Transmission (HTTP POST + Wi-Fi TX Burst):**
  $$I_{scan} \approx 240\text{ mA (Wi-Fi)} + 20\text{ mA (RC522)} + 15\text{ mA (OLED)} + 5\text{ mA} = \mathbf{280\text{ mA (average)}}$$
  *Transient instantaneous peak: $\mathbf{420\text{ mA} - 480\text{ mA}}$.*
- **Regime C: Simultaneous Scan + Audio Feedback (Wi-Fi retry + Buzzer sounding):**
  $$I_{worst} \approx 240\text{ mA} + 20\text{ mA} + 15\text{ mA} + 35\text{ mA (Buzzer)} + 5\text{ mA} = \mathbf{315\text{ mA (average)}}$$
  *Transient instantaneous peak: $\mathbf{460\text{ mA} - 520\text{ mA}}$.*

### 6.4 Mandatory Engineering Safeguards
To guarantee rock-solid stability and prevent brownout crashes:
1. **Breadboard Bulk Decoupling Capacitor:** Insert a **$47\,\mu\text{F}$ to $100\,\mu\text{F}$ electrolytic or tantalum capacitor** (rated $\ge 10\text{V}$) directly across the breadboard's 3.3V and GND rails adjacent to the ESP32 power pins. This acts as a low-impedance local reservoir supplying the microsecond Wi-Fi surge current.
2. **Direct Motherboard USB Port:** Plug the USB cable directly into a rear motherboard USB 3.0 or USB 2.0 port. **Do NOT** use unpowered USB hubs, keyboard passthroughs, or front-panel case extension cables.
3. **Cable Quality Verification:** If brownout resets are observed during Wi-Fi transmission, replace the flat USB cable with a short, round, heavy-gauge (24 AWG power) USB data cable.

---

## 7. Remaining Physical Verification Requirements (Laboratory Checklist)

Prior to applying power or writing any firmware code in Stage 10.2, the following physical verification checklist must be executed on the workbench:

```
[ ] STEP 1: SOLDER OLED HEADER
    - Solder the 4-pin male header strip cleanly to OLED pads 1-4.
    - Inspect solder joints under light to confirm no solder bridges between adjacent pins.

[ ] STEP 2: VERIFY ACTIVE BUZZER MODULE
    - Photograph SOT-23 transistor under magnification to read marking code.
    - Perform bench check with 3.3V supply to determine logic polarity (Active-HIGH vs Active-LOW).
    - Measure control pin input current (< 5 mA required).

[ ] STEP 3: ASSEMBLE COMMON GROUND BREADBOARD LAYOUT
    - Place ESP32 across breadboard center dividing trough.
    - Connect single continuous GND rail across the breadboard.
    - Insert 47 uF - 100 uF electrolytic capacitor across 3.3V and GND rails (observe polarity: negative stripe to GND).

[ ] STEP 4: WIRE BUS LINES ACCORDING TO REVISED PINOUT
    - RC522: SCK -> GPIO18, MOSI -> GPIO23, MISO -> GPIO19, RST -> GPIO4.
    - RC522 SS: Wire to GPIO27 (replaces GPIO5).
    - RC522 VCC: Wire to 3.3V rail ONLY.
    - OLED: SCK -> GPIO22, SDA -> GPIO21, VDD -> 3.3V rail, GND -> GND rail.
    - Buzzer: Signal -> GPIO25, VCC -> verified rail, GND -> GND rail.

[ ] STEP 5: PRE-POWER MULTIMETER VERIFICATION
    - Measure unpowered resistance between 3.3V rail and GND rail (must show charging curve / open circuit, NOT 0 ohms).
    - Apply USB power to ESP32 (peripherals unattached or isolated).
    - Measure 3V3 pin: Confirm steady 3.30V +/- 0.10V.
```

---

## 8. Summary of Corrections to Stage 10.1 Findings

| Item / Finding | Stage 10.1 Previous Statement | Stage 10.1A Corrected Finding | Operational Status |
| :--- | :--- | :--- | :--- |
| **GPIO5 Strapping Status** | Claimed GPIO5 has no strapping role. | **GPIO5 is an ESP32 strapping pin** controlling SDIO slave timing and outputs clock glitch at boot. | Reassign RC522 SS to **GPIO27** in Stage 10.2. |
| **Buzzer Driver Circuit** | Claimed module contains an S8050 transistor. | **Unverified assumption.** SOT-23 package marking is illegible. Transistor type and logic polarity unknown. | Physical bench check required to determine polarity. |
| **OLED Header State** | Not discussed. | **4-pin header is physically unsoldered** in user photos. | Must solder header before breadboard assembly. |
| **OLED Pin Mapping** | Presumed standard. | Confirmed from silkscreen: 1:GND, 2:VDD (3.3V), 3:SCK (GPIO22), 4:SDA (GPIO21). | Validated against SSD1306 hardware I2C contract. |
| **Power Budget Reliability** | Claimed 339 mA peak has 32.2% margin on 500 mA USB. | Discredited. Cable IR drop + LDO dropout can cause brownout during 450 mA Wi-Fi bursts. | Add 47–100 uF bulk capacitor; use rear USB port. |

---

## 9. Conclusion & Preflight Sign-Off

The hardware review and photographic inspection for Stage 10.1A are **complete**. The critical GPIO5 strapping vulnerability has been diagnosed and resolved via the GPIO27 reassignment proposal. Component-level uncertainties regarding the buzzer module and unsoldered OLED header have been clearly documented with concrete laboratory verification steps.

**STOP CONDITION ENFORCED:** As mandated, this stage is an audit and correction only. No firmware files have been modified, no hardware has been wired or flashed, and Stage 10.2 has not been started.

/**
 * @file rfid_scanner.cpp
 * @brief Implementation of RC522 RFID Reader Controller for ESP32.
 */

#include "rfid_scanner.h"

// Global scanner instance
RFIDScanner rfid;

RFIDScanner::RFIDScanner()
    : _mfrc522(RFID_SS_PIN, RFID_RST_PIN),
      _lastUid(""),
      _lastScanTime(0),
      _hasScanned(false) {
}

bool RFIDScanner::begin() {
    // Initialize SPI bus with custom ESP32 pins
    SPI.begin(RFID_SCK_PIN, RFID_MISO_PIN, RFID_MOSI_PIN, RFID_SS_PIN);

    // Initialize MFRC522 reader
    _mfrc522.PCD_Init();
    delay(10); // Allow hardware to stabilize

    // Verify SPI communication by reading MFRC522 firmware version
    byte version = _mfrc522.PCD_ReadRegister(MFRC522::VersionReg);
    DEBUG_PRINTF("[RFID] MFRC522 Firmware Version: 0x%02X\n", version);

    // 0x00 or 0xFF indicates SPI communication failure / wiring issue
    if (version == 0x00 || version == 0xFF) {
        DEBUG_PRINTLN("[RFID] ERROR: Communication failed! Check SPI wiring and 3.3V power.");
        return false;
    }

    DEBUG_PRINTLN("[RFID] RC522 reader initialized and online.");
    return true;
}

bool RFIDScanner::isValidUid(const String& uid) {
    // 1. Length validation matching backend API contract: [4, 50] characters
    // Hardware MIFARE Classic UIDs are typically 8 hex characters (4 bytes)
    // MIFARE Ultralight / NTAG UIDs are 14 hex characters (7 bytes)
    size_t len = uid.length();
    if (len < 4 || len > 50) {
        return false;
    }

    // 2. Strict character validation: must be uppercase hexadecimal characters [0-9A-F]
    for (size_t i = 0; i < len; i++) {
        char c = uid.charAt(i);
        if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F'))) {
            return false;
        }
    }

    return true;
}

String RFIDScanner::formatUid(MFRC522::Uid* uid) {
    if (!uid || uid->size == 0 || uid->size > 10) return "";

    String hexUid = "";
    char hexByte[3];

    for (byte i = 0; i < uid->size; i++) {
        // %02X guarantees 2 uppercase hex digits per byte, preserving leading zeros
        sprintf(hexByte, "%02X", uid->uidByte[i]);
        hexUid += hexByte;
    }

    return hexUid;
}

bool RFIDScanner::readCard(String& outUid) {
    // 1. Check if an RFID card is present in the RF field
    if (!_mfrc522.PICC_IsNewCardPresent()) {
        return false;
    }

    // 2. Read the card's serial / UID bytes
    if (!_mfrc522.PICC_ReadCardSerial()) {
        // Read attempt failed (card moved too quickly or communication glitch)
        return false;
    }

    // 3. Format UID into uppercase hex string preserving leading zeros
    String currentUid = formatUid(&(_mfrc522.uid));

    // 4. Strict local UID validation before any debounce or transmission
    if (!isValidUid(currentUid)) {
        DEBUG_PRINTLN("[RFID] ERROR: Corrupted or invalid UID format rejected: " + currentUid);
        haltCard();
        return false;
    }

    unsigned long now = millis();

    // 5. Debounce & Anti-Flooding Logic
    // Fix: First scan after startup must NEVER be debounced (_hasScanned starts false)
    if (_hasScanned) {
        // Safe unsigned arithmetic handles millis() rollover naturally
        unsigned long elapsed = now - _lastScanTime;

        if (currentUid.equals(_lastUid)) {
            // Same card held against reader antenna: prevent scan flooding
            if (elapsed < SAME_CARD_HOLD_MS) {
                DEBUG_PRINTLN("[RFID] Debounce: Same card held on reader. Suppressing flood request.");
                haltCard();
                return false;
            }
        } else {
            // Different card: apply rapid scan debounce interval
            if (elapsed < SCAN_DEBOUNCE_MS) {
                DEBUG_PRINTLN("[RFID] Debounce: Rapid scan detected. Waiting for interval to elapse.");
                haltCard();
                return false;
            }
        }
    }

    // 6. Valid fresh card scan accepted
    _hasScanned = true;
    _lastScanTime = now;
    _lastUid = currentUid;
    outUid = currentUid;

    DEBUG_PRINTLN("[RFID] Valid card detected: " + outUid);

    // 7. Halt card to end communication cycle and stop crypto cipher
    haltCard();
    return true;
}

void RFIDScanner::haltCard() {
    _mfrc522.PICC_HaltA();
    _mfrc522.PCD_StopCrypto1();
}

void RFIDScanner::resetDebounce() {
    _lastUid = "";
    _lastScanTime = 0;
    _hasScanned = false;
}

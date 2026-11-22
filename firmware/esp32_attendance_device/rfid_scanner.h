/**
 * @file rfid_scanner.h
 * @brief RC522 RFID Reader Controller for Smart Attendance System.
 * 
 * Manages SPI bus initialization, card detection, UID normalization to uppercase
 * hexadecimal strings, and physical card hold debounce to prevent scan flooding.
 */

#ifndef RFID_SCANNER_H
#define RFID_SCANNER_H

#include <Arduino.h>
#include <SPI.h>
#include <MFRC522.h>
#include "config.h"

class RFIDScanner {
public:
    RFIDScanner();

    /**
     * @brief Initializes SPI bus and MFRC522 chip.
     * @return true if communication succeeded, false otherwise.
     */
    bool begin();

    /**
     * @brief Checks if an RFID card is present and ready to be read.
     * Implements hold debounce to prevent duplicate requests when a card is held on the reader.
     * 
     * @param outUid Output reference where normalized uppercase hex UID will be stored.
     * @return true if a valid fresh card scan occurred, false if no card or debounced.
     */
    bool readCard(String& outUid);

    /**
     * @brief Halts the card and stops crypto to ready the reader for next detection.
     */
    void haltCard();

    /**
     * @brief Resets debounce memory (e.g. for testing or after long idle).
     */
    void resetDebounce();

    /**
     * @brief Returns the last read card UID.
     */
    String getLastUid() const { return _lastUid; }

    /**
     * @brief Masks an RFID UID string for diagnostic logging to protect privacy.
     * Example: "A1B2C3D4" -> "A1****D4", "1234" -> "****"
     * 
     * @param uid Full hexadecimal UID string.
     * @return Sanitized/masked string safe for production serial logs.
     */
    static String maskUid(const String& uid);

    /**
     * @brief Validates that a candidate UID string satisfies backend API rules
     * and hardware integrity constraints:
     * - Must not be empty.
     * - Must contain only uppercase hexadecimal characters [0-9A-F].
     * - Length must be between 4 and 50 characters (typical hex UIDs are 8, 14, or 20 chars).
     * 
     * @param uid The UID string to validate.
     * @return true if valid, false if empty, malformed, or out of length bounds.
     */
    static bool isValidUid(const String& uid);

private:
    MFRC522 _mfrc522;
    String _lastUid;
    unsigned long _lastScanTime;
    bool _hasScanned;  ///< State flag: false until first valid scan is accepted

    String formatUid(MFRC522::Uid* uid);
};

// Global scanner singleton
extern RFIDScanner rfid;

#endif // RFID_SCANNER_H

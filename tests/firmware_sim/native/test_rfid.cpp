/**
 * @file test_rfid.cpp
 * @brief Comprehensive Unit Tests for RFIDScanner (rfid_scanner.cpp).
 * 
 * Tests:
 * 1. UID formatting (4-byte, 7-byte, 10-byte, leading zeros, byte order).
 * 2. UID validation (bounds [4, 50], uppercase hex, invalid characters).
 * 3. Hold debounce (5000ms), rapid debounce (2000ms), exact threshold & ±1ms.
 * 4. Millis rollover handling in debounce timing.
 * 5. Hardware failure simulation (no card, read failure, version 0x00/0xFF).
 * 6. Card halt and crypto cleanup verification.
 */

#include "test_framework.h"
#include "Arduino.h"
#include "SPI.h"
#include "MFRC522.h"
#include "config.h"
#include "rfid_scanner.h"

// Helper to ensure clean RFID test state
static void setupRfidTest() {
    mockResetMillis();
    rfid.resetDebounce();
    if (g_lastMfrc522Instance) {
        g_lastMfrc522Instance->mockReset();
    }
}

// =============================================================================
// PART 1.1: UID Formatting Tests
// =============================================================================

TEST_CASE(RFID_Format4ByteUid) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);

    // 4-byte MIFARE Classic UID: 0x1A, 0x2B, 0x3C, 0x4D
    const byte uid4[] = { 0x1A, 0x2B, 0x3C, 0x4D };
    g_lastMfrc522Instance->mockInjectUid(uid4, 4);

    String readUid;
    bool ok = rfid.readCard(readUid);

    ASSERT_TRUE(ok);
    ASSERT_EQ(8u, readUid.length());
    ASSERT_STR_EQ("1A2B3C4D", readUid.c_str());
}

TEST_CASE(RFID_Format7ByteUid) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);

    // 7-byte MIFARE Ultralight / NTAG UID: 0x04, 0x5A, 0x6B, 0x7C, 0x8D, 0x9E, 0x0F
    const byte uid7[] = { 0x04, 0x5A, 0x6B, 0x7C, 0x8D, 0x9E, 0x0F };
    g_lastMfrc522Instance->mockInjectUid(uid7, 7);

    String readUid;
    bool ok = rfid.readCard(readUid);

    ASSERT_TRUE(ok);
    ASSERT_EQ(14u, readUid.length());
    ASSERT_STR_EQ("045A6B7C8D9E0F", readUid.c_str());
}

TEST_CASE(RFID_Format10ByteUid) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);

    // 10-byte UID (maximum supported size)
    const byte uid10[] = { 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A };
    g_lastMfrc522Instance->mockInjectUid(uid10, 10);

    String readUid;
    bool ok = rfid.readCard(readUid);

    ASSERT_TRUE(ok);
    ASSERT_EQ(20u, readUid.length());
    ASSERT_STR_EQ("0102030405060708090A", readUid.c_str());
}

TEST_CASE(RFID_FormatLeadingZerosAndByteOrder) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);

    // Byte 0 is 0x00, Byte 1 is 0x05, Byte 2 is 0x0A, Byte 3 is 0xFF
    const byte uidZeros[] = { 0x00, 0x05, 0x0A, 0xFF };
    g_lastMfrc522Instance->mockInjectUid(uidZeros, 4);

    String readUid;
    bool ok = rfid.readCard(readUid);

    ASSERT_TRUE(ok);
    // Verifies leading zeros are preserved ("00", "05", "0A") and byte order (byte 0 first)
    ASSERT_STR_EQ("00050AFF", readUid.c_str());
}

TEST_CASE(RFID_FormatUppercaseHexOnly) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);

    const byte uidHex[] = { 0xAB, 0xCD, 0xEF, 0x12 };
    g_lastMfrc522Instance->mockInjectUid(uidHex, 4);

    String readUid;
    rfid.readCard(readUid);

    ASSERT_STR_EQ("ABCDEF12", readUid.c_str());
    // Verify all alphabetic characters are strictly uppercase
    for (size_t i = 0; i < readUid.length(); i++) {
        char c = readUid.charAt(i);
        if (c >= 'a' && c <= 'z') {
            ASSERT_TRUE(false); // Must not contain lowercase letters
        }
    }
}

// =============================================================================
// PART 1.2: UID Validation Tests (RFIDScanner::isValidUid)
// =============================================================================

TEST_CASE(RFID_ValidationAcceptedBoundaries) {
    // Exact minimum accepted boundary: 4 characters
    ASSERT_TRUE(RFIDScanner::isValidUid("A1B2"));
    ASSERT_FALSE(RFIDScanner::isValidUid("A1B")); // 3 chars rejected

    // Exact maximum accepted boundary: 50 characters
    std::string valid50(50, 'F');
    ASSERT_TRUE(RFIDScanner::isValidUid(String(valid50.c_str())));

    // One character above maximum boundary: 51 characters
    std::string invalid51(51, 'F');
    ASSERT_FALSE(RFIDScanner::isValidUid(String(invalid51.c_str())));
}

TEST_CASE(RFID_ValidationEmptyAndNull) {
    ASSERT_FALSE(RFIDScanner::isValidUid(""));
    ASSERT_FALSE(RFIDScanner::isValidUid(String()));
}

TEST_CASE(RFID_ValidationInvalidCharacters) {
    // Lowercase hex rejected by strict uppercase contract
    ASSERT_FALSE(RFIDScanner::isValidUid("1a2b3c4d"));
    ASSERT_FALSE(RFIDScanner::isValidUid("1A2B3c4D"));

    // Non-hex characters
    ASSERT_FALSE(RFIDScanner::isValidUid("1A2G3C4D")); // 'G'
    ASSERT_FALSE(RFIDScanner::isValidUid("1A2Z3C4D")); // 'Z'
    ASSERT_FALSE(RFIDScanner::isValidUid("1A2B 3C4D")); // Space
    ASSERT_FALSE(RFIDScanner::isValidUid("1A2B-3C4D")); // Hyphen
    ASSERT_FALSE(RFIDScanner::isValidUid("1A2B:3C4D")); // Colon
    ASSERT_FALSE(RFIDScanner::isValidUid("1A2B.3C4D")); // Dot
    ASSERT_FALSE(RFIDScanner::isValidUid("!@#$%^&*")); // Symbols
}

TEST_CASE(RFID_ValidationOddLengthString) {
    // In production code, length must be in [4, 50].
    // An odd length string of 5 valid hex characters satisfies the contract.
    ASSERT_TRUE(RFIDScanner::isValidUid("12345"));
    ASSERT_TRUE(RFIDScanner::isValidUid("ABCDE"));
}

// =============================================================================
// PART 1.3: Debounce & Anti-Flooding Tests
// =============================================================================

TEST_CASE(RFID_DebounceFirstScanNeverSuppressed) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);
    g_lastMfrc522Instance->mockInjectUidHex("12345678");

    // Even at millis() == 0, the first scan after boot must never be debounced
    mockSetMillis(0);
    String outUid;
    bool ok = rfid.readCard(outUid);

    ASSERT_TRUE(ok);
    ASSERT_STR_EQ("12345678", outUid.c_str());
    ASSERT_STR_EQ("12345678", rfid.getLastUid().c_str());
}

TEST_CASE(RFID_DebounceSameCardHoldPacing) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);
    g_lastMfrc522Instance->mockInjectUidHex("AABBCCDD");

    mockSetMillis(1000);
    String outUid;
    ASSERT_TRUE(rfid.readCard(outUid)); // First scan accepted at t=1000ms

    // Hold card against reader: immediately next cycle (t=1100ms, elapsed 100ms)
    mockSetMillis(1100);
    ASSERT_FALSE(rfid.readCard(outUid)); // Debounced!

    // Intermediate poll (t=3000ms, elapsed 2000ms < 5000ms)
    mockSetMillis(3000);
    ASSERT_FALSE(rfid.readCard(outUid)); // Debounced!

    // Exactly 1ms below threshold (t=5999ms, elapsed 4999ms < 5000ms)
    mockSetMillis(5999);
    ASSERT_FALSE(rfid.readCard(outUid)); // Debounced!

    // Exact threshold (t=6000ms, elapsed 5000ms == SAME_CARD_HOLD_MS)
    mockSetMillis(6000);
    ASSERT_TRUE(rfid.readCard(outUid)); // Accepted!

    // Exactly 1ms above threshold (t=11001ms, elapsed 5001ms > 5000ms)
    mockSetMillis(11001);
    ASSERT_TRUE(rfid.readCard(outUid)); // Accepted!
}

TEST_CASE(RFID_DebounceDifferentCardRapidScanPacing) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);

    // Card 1 scanned at t=10000ms
    g_lastMfrc522Instance->mockInjectUidHex("11111111");
    mockSetMillis(10000);
    String outUid;
    ASSERT_TRUE(rfid.readCard(outUid));
    ASSERT_STR_EQ("11111111", outUid.c_str());

    // Card 2 presented at t=10500ms (elapsed 500ms < 2000ms)
    g_lastMfrc522Instance->mockInjectUidHex("22222222");
    mockSetMillis(10500);
    ASSERT_FALSE(rfid.readCard(outUid)); // Rapid scan debounced!

    // Exactly 1ms below threshold (t=11999ms, elapsed 1999ms < 2000ms)
    mockSetMillis(11999);
    ASSERT_FALSE(rfid.readCard(outUid)); // Debounced!

    // Exact threshold (t=12000ms, elapsed 2000ms == SCAN_DEBOUNCE_MS)
    mockSetMillis(12000);
    ASSERT_TRUE(rfid.readCard(outUid)); // Accepted!
    ASSERT_STR_EQ("22222222", outUid.c_str());

    // Card 3 presented 1ms above threshold (t=14001ms, elapsed 2001ms > 2000ms)
    g_lastMfrc522Instance->mockInjectUidHex("33333333");
    mockSetMillis(14001);
    ASSERT_TRUE(rfid.readCard(outUid)); // Accepted!
    ASSERT_STR_EQ("33333333", outUid.c_str());
}

TEST_CASE(RFID_DebounceMillisRollover) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);
    g_lastMfrc522Instance->mockInjectUidHex("A1B2C3D4");

    // Scan occurs right before uint32_t rollover
    uint32_t preRollover = 0xFFFFFFF0; // 16ms before 2^32-1
    mockSetMillis(preRollover);
    String outUid;
    ASSERT_TRUE(rfid.readCard(outUid));

    // Post-rollover: current millis wraps around to 0x00000010 (32ms total elapsed)
    uint32_t postRollover = 0x00000010;
    mockSetMillis(postRollover);
    // 32ms < SAME_CARD_HOLD_MS (5000ms) -> must be debounced
    ASSERT_FALSE(rfid.readCard(outUid));

    // Advance post-rollover to elapsed >= 5000ms:
    // (postRollover + (5000 - 32)) = 0x10 + 4968 = 4984 (0x00001378)
    mockSetMillis(0x00001378);
    ASSERT_TRUE(rfid.readCard(outUid)); // Unsigned rollover arithmetic correctly detects elapsed >= 5000!
}

// =============================================================================
// PART 1.4: Hardware Failure Simulation Tests
// =============================================================================

TEST_CASE(RFID_HardwareNoCardPresent) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(false);

    String outUid;
    bool ok = rfid.readCard(outUid);

    ASSERT_FALSE(ok);
    ASSERT_EQ(0u, outUid.length());
}

TEST_CASE(RFID_HardwareReadSerialFailure) {
    setupRfidTest();
    // Card present in RF field, but communication glitch during serial read
    g_lastMfrc522Instance->mockSetCardPresent(true);
    g_lastMfrc522Instance->mockSetReadSerialSuccess(false);

    String outUid;
    bool ok = rfid.readCard(outUid);

    ASSERT_FALSE(ok);
    ASSERT_EQ(0u, outUid.length());
}

TEST_CASE(RFID_HardwareVersionRegisterCheck) {
    setupRfidTest();

    // Version register 0x00 indicates disconnected / unpowered RC522
    g_lastMfrc522Instance->mockSetVersion(0x00);
    ASSERT_FALSE(rfid.begin());

    // Version register 0xFF indicates SPI bus floating / wiring fault
    g_lastMfrc522Instance->mockSetVersion(0xFF);
    ASSERT_FALSE(rfid.begin());

    // Valid firmware version (0x92 = MFRC522 v2.0)
    g_lastMfrc522Instance->mockSetVersion(0x92);
    ASSERT_TRUE(rfid.begin());
}

TEST_CASE(RFID_CardHaltAndCryptoCleanup) {
    setupRfidTest();
    g_lastMfrc522Instance->mockSetCardPresent(true);
    g_lastMfrc522Instance->mockInjectUidHex("11223344");

    String outUid;
    rfid.readCard(outUid);

    // After a successful read, PICC_HaltA() and PCD_StopCrypto1() must be called
    ASSERT_TRUE(g_lastMfrc522Instance->mockWasHalted());
    ASSERT_TRUE(g_lastMfrc522Instance->mockWasCryptoStopped());

    // Explicit haltCard() call
    rfid.haltCard();
    ASSERT_TRUE(g_lastMfrc522Instance->mockGetHaltCount() >= 2);
}

// =============================================================================
// PART 1.4: Privacy Masking & Diagnostic Logging Security
// =============================================================================

TEST_CASE(RFID_MaskUidStandardAndEdgeCases) {
    // 8-character standard UID
    ASSERT_STR_EQ("A1****D4", RFIDScanner::maskUid("A1B2C3D4").c_str());
    // 14-character 7-byte UID
    ASSERT_STR_EQ("04****0F", RFIDScanner::maskUid("045A6B7C8D9E0F").c_str());
    // 20-character 10-byte UID
    ASSERT_STR_EQ("01****0A", RFIDScanner::maskUid("0102030405060708090A").c_str());
    // Exactly 4 characters -> fully masked
    ASSERT_STR_EQ("****", RFIDScanner::maskUid("1234").c_str());
    // Short UIDs (<= 4 chars) -> fully masked
    ASSERT_STR_EQ("****", RFIDScanner::maskUid("ABC").c_str());
    ASSERT_STR_EQ("****", RFIDScanner::maskUid("").c_str());
}

TEST_CASE(RFID_LoggingDoesNotExposeRawUid) {
    setupRfidTest();
    Serial.mockClearOutput();

    g_lastMfrc522Instance->mockSetCardPresent(true);
    g_lastMfrc522Instance->mockInjectUidHex("A1B2C3D4");

    String outUid;
    bool ok = rfid.readCard(outUid);

    ASSERT_TRUE(ok);
    ASSERT_STR_EQ("A1B2C3D4", outUid.c_str()); // Raw UID preserved for backend

    // Verify Serial output contains masked UID but NOT the raw unmasked UID
    std::string serialLog = Serial.mockGetOutput();
    ASSERT_TRUE(serialLog.find("A1****D4") != std::string::npos);
    ASSERT_TRUE(serialLog.find("A1B2C3D4") == std::string::npos);
}

TEST_CASE(RFID_InvalidUidLoggingMasksCorruptedValue) {
    setupRfidTest();
    Serial.mockClearOutput();

    // Inject invalid short UID (1 byte = 2 hex chars: "12")
    g_lastMfrc522Instance->mockSetCardPresent(true);
    byte rawBytes[] = { 0x12 };
    g_lastMfrc522Instance->mockInjectUid(rawBytes, 1);

    String outUid;
    bool ok = rfid.readCard(outUid);

    ASSERT_FALSE(ok);
    std::string serialLog = Serial.mockGetOutput();
    ASSERT_TRUE(serialLog.find("****") != std::string::npos);
}

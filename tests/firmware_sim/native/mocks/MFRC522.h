/**
 * @file MFRC522.h
 * @brief Host-side Mock for MFRC522 RFID Library.
 */

#ifndef MFRC522_H
#define MFRC522_H

#include "Arduino.h"

class MFRC522;
inline MFRC522* g_lastMfrc522Instance = nullptr;

class MFRC522 {
public:
    enum PCD_Register : byte {
        VersionReg = 0x37
    };

    struct Uid {
        byte size = 0;
        byte uidByte[10] = {0};
        byte sak = 0;
    };

    Uid uid;

    int ssPin = -1;
    int rstPin = -1;
    bool isPcdInitialized = false;
    byte mockFirmwareVersion = 0x92; // 0x92 = MFRC522 v2.0
    bool mockCardPresent = false;
    bool mockReadSerialSuccess = true;
    bool mockCardHalted = false;
    bool mockCryptoStopped = false;
    int mockHaltCount = 0;

    MFRC522(int ss = -1, int rst = -1) : ssPin(ss), rstPin(rst) {
        g_lastMfrc522Instance = this;
    }

    ~MFRC522() {
        if (g_lastMfrc522Instance == this) {
            g_lastMfrc522Instance = nullptr;
        }
    }

    void PCD_Init() {
        isPcdInitialized = true;
    }

    byte PCD_ReadRegister(byte reg) {
        if (reg == VersionReg) {
            return mockFirmwareVersion;
        }
        return 0x00;
    }

    byte PCD_ReadRegister(PCD_Register reg) {
        return PCD_ReadRegister(static_cast<byte>(reg));
    }

    bool PICC_IsNewCardPresent() {
        return mockCardPresent;
    }

    bool PICC_ReadCardSerial() {
        return mockCardPresent && mockReadSerialSuccess;
    }

    void PICC_HaltA() {
        mockCardHalted = true;
        mockHaltCount++;
    }

    void PCD_StopCrypto1() {
        mockCryptoStopped = true;
    }

    // =========================================================================
    // Mock Configuration & Inspection Hooks
    // =========================================================================
    void mockSetVersion(byte v) {
        mockFirmwareVersion = v;
    }

    void mockSetCardPresent(bool present) {
        mockCardPresent = present;
    }

    void mockSetReadSerialSuccess(bool success) {
        mockReadSerialSuccess = success;
    }

    void mockInjectUid(const byte* bytes, byte size) {
        uid.size = (size > 10) ? 10 : size;
        for (byte i = 0; i < uid.size; i++) {
            uid.uidByte[i] = bytes[i];
        }
    }

    void mockInjectUidHex(const char* hexStr) {
        if (!hexStr) {
            uid.size = 0;
            return;
        }
        size_t len = strlen(hexStr);
        byte count = 0;
        for (size_t i = 0; i + 1 < len && count < 10; i += 2) {
            char byteStr[3] = { hexStr[i], hexStr[i+1], '\0' };
            uid.uidByte[count++] = static_cast<byte>(strtoul(byteStr, nullptr, 16));
        }
        uid.size = count;
    }

    bool mockWasHalted() const { return mockCardHalted; }
    bool mockWasCryptoStopped() const { return mockCryptoStopped; }
    int mockGetHaltCount() const { return mockHaltCount; }

    void mockReset() {
        isPcdInitialized = false;
        mockFirmwareVersion = 0x92;
        mockCardPresent = false;
        mockReadSerialSuccess = true;
        mockCardHalted = false;
        mockCryptoStopped = false;
        mockHaltCount = 0;
        uid.size = 0;
        for (int i = 0; i < 10; i++) uid.uidByte[i] = 0;
    }
};

#endif // MFRC522_H

/**
 * @file Wire.h
 * @brief Host-side Mock for Arduino Wire (I2C) Library.
 */

#ifndef WIRE_H
#define WIRE_H

#include "Arduino.h"

class TwoWire {
public:
    bool isInitialized = false;
    int sdaPin = -1;
    int sclPin = -1;
    uint32_t clockFreq = 100000;

    bool begin(int sda = -1, int scl = -1) {
        isInitialized = true;
        sdaPin = sda;
        sclPin = scl;
        return true;
    }

    void setClock(uint32_t freq) {
        clockFreq = freq;
    }

    void mockReset() {
        isInitialized = false;
        sdaPin = -1;
        sclPin = -1;
        clockFreq = 100000;
    }
};

inline TwoWire Wire;

#endif // WIRE_H

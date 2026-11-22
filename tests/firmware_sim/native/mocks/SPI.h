/**
 * @file SPI.h
 * @brief Host-side Mock for Arduino SPI Library.
 */

#ifndef SPI_H
#define SPI_H

#include "Arduino.h"

class SPIClass {
public:
    bool isInitialized = false;
    int sckPin = -1;
    int misoPin = -1;
    int mosiPin = -1;
    int ssPin = -1;

    void begin(int sck = -1, int miso = -1, int mosi = -1, int ss = -1) {
        isInitialized = true;
        sckPin = sck;
        misoPin = miso;
        mosiPin = mosi;
        ssPin = ss;
    }

    void end() {
        isInitialized = false;
    }

    void mockReset() {
        isInitialized = false;
        sckPin = -1;
        misoPin = -1;
        mosiPin = -1;
        ssPin = -1;
    }
};

inline SPIClass SPI;

#endif // SPI_H

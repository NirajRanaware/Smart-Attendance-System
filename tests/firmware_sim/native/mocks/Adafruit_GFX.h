/**
 * @file Adafruit_GFX.h
 * @brief Host-side Mock for Adafruit GFX Library.
 */

#ifndef ADAFRUIT_GFX_H
#define ADAFRUIT_GFX_H

#include "Arduino.h"

class Adafruit_GFX {
public:
    int16_t WIDTH, HEIGHT;
    int16_t _width, _height;
    int16_t cursor_x, cursor_y;
    uint16_t textcolor, textbgcolor;
    uint8_t textsize_x, textsize_y;
    bool wrap;

    Adafruit_GFX(int16_t w, int16_t h)
        : WIDTH(w), HEIGHT(h), _width(w), _height(h),
          cursor_x(0), cursor_y(0), textcolor(1), textbgcolor(0),
          textsize_x(1), textsize_y(1), wrap(true) {}

    virtual ~Adafruit_GFX() {}

    int16_t width() const { return _width; }
    int16_t height() const { return _height; }
};

#endif // ADAFRUIT_GFX_H

/**
 * @file Adafruit_SSD1306.h
 * @brief Host-side Mock for Adafruit SSD1306 OLED Display Library.
 */

#ifndef ADAFRUIT_SSD1306_H
#define ADAFRUIT_SSD1306_H

#include "Arduino.h"
#include "Wire.h"
#include "Adafruit_GFX.h"
#include <vector>
#include <string>

#define SSD1306_SWITCHCAPVCC 0x02
#define SSD1306_WHITE 1
#define SSD1306_BLACK 0
#define WHITE 1
#define BLACK 0

class Adafruit_SSD1306;
inline Adafruit_SSD1306* g_lastOledInstance = nullptr;

class Adafruit_SSD1306 : public Adafruit_GFX {
public:
    TwoWire* wire = nullptr;
    int8_t resetPin = -1;
    bool isInitialized = false;
    bool beginResult = true;
    uint32_t displayCount = 0;
    uint32_t clearCount = 0;
    std::vector<std::string> printedStrings;
    std::string currentFrameText;

    Adafruit_SSD1306(uint16_t w, uint16_t h, TwoWire *twi = &Wire, int8_t rst_pin = -1)
        : Adafruit_GFX(w, h), wire(twi), resetPin(rst_pin) {
        g_lastOledInstance = this;
    }

    virtual ~Adafruit_SSD1306() {
        if (g_lastOledInstance == this) {
            g_lastOledInstance = nullptr;
        }
    }

    bool begin(uint8_t switchvcc = SSD1306_SWITCHCAPVCC, uint8_t i2caddr = 0x3C,
               bool reset = true, bool periphBegin = true) {
        (void)switchvcc;
        (void)i2caddr;
        (void)reset;
        (void)periphBegin;
        isInitialized = beginResult;
        return beginResult;
    }

    void clearDisplay() {
        clearCount++;
        currentFrameText.clear();
    }

    void display() {
        displayCount++;
    }

    void setTextColor(uint16_t c) {
        textcolor = c;
        textbgcolor = (c == SSD1306_WHITE) ? SSD1306_BLACK : SSD1306_WHITE;
    }

    void setTextColor(uint16_t c, uint16_t bg) {
        textcolor = c;
        textbgcolor = bg;
    }

    void setTextWrap(bool w) {
        wrap = w;
    }

    void setTextSize(uint8_t s) {
        textsize_x = s;
        textsize_y = s;
    }

    void setCursor(int16_t x, int16_t y) {
        cursor_x = x;
        cursor_y = y;
    }

    void print(const String& s) {
        printedStrings.push_back(s.c_str());
        currentFrameText += s.c_str();
    }

    void print(const char* s) {
        if (s) {
            printedStrings.push_back(s);
            currentFrameText += s;
        }
    }

    void print(int n) {
        std::string s = std::to_string(n);
        printedStrings.push_back(s);
        currentFrameText += s;
    }

    void print(int8_t n) {
        std::string s = std::to_string(static_cast<int>(n));
        printedStrings.push_back(s);
        currentFrameText += s;
    }

    void print(uint32_t n) {
        std::string s = std::to_string(n);
        printedStrings.push_back(s);
        currentFrameText += s;
    }

    void println(const String& s) {
        print(s);
        currentFrameText += "\n";
    }

    void println(const char* s) {
        print(s);
        currentFrameText += "\n";
    }

    void println() {
        currentFrameText += "\n";
    }

    void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
        (void)x; (void)y; (void)w; (void)h; (void)color;
    }

    void drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
        (void)x; (void)y; (void)w; (void)h; (void)color;
    }

    void drawFastHLine(int16_t x, int16_t y, int16_t w, uint16_t color) {
        (void)x; (void)y; (void)w; (void)color;
    }

    void getTextBounds(const char *string, int16_t x, int16_t y,
                       int16_t *x1, int16_t *y1, uint16_t *w, uint16_t *h) {
        if (!string) {
            if (x1) *x1 = x;
            if (y1) *y1 = y;
            if (w) *w = 0;
            if (h) *h = 0;
            return;
        }
        size_t len = strlen(string);
        // Standard 6x8 font approximation per character (5 pixels + 1 spacing)
        uint16_t width = static_cast<uint16_t>(len * 6 * textsize_x);
        uint16_t height = static_cast<uint16_t>(8 * textsize_y);
        if (x1) *x1 = x;
        if (y1) *y1 = y;
        if (w) *w = width;
        if (h) *h = height;
    }

    void getTextBounds(const String &str, int16_t x, int16_t y,
                       int16_t *x1, int16_t *y1, uint16_t *w, uint16_t *h) {
        getTextBounds(str.c_str(), x, y, x1, y1, w, h);
    }

    // =========================================================================
    // Mock Controls
    // =========================================================================
    void mockSetBeginSuccess(bool success) {
        beginResult = success;
    }

    const std::string& mockGetCurrentFrameText() const {
        return currentFrameText;
    }

    const std::vector<std::string>& mockGetPrintedStrings() const {
        return printedStrings;
    }

    void mockReset() {
        isInitialized = false;
        beginResult = true;
        displayCount = 0;
        clearCount = 0;
        printedStrings.clear();
        currentFrameText.clear();
    }
};

#endif // ADAFRUIT_SSD1306_H

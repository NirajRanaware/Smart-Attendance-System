/**
 * @file Arduino.h
 * @brief Host-side Mock for Arduino Framework.
 * 
 * Provides mock implementations of core Arduino types, GPIO, timing,
 * Serial logging, and a full-featured Arduino String implementation
 * to allow native host compilation with g++ without Arduino hardware.
 */

#ifndef ARDUINO_H
#define ARDUINO_H

#ifndef ARDUINO
#define ARDUINO 10819
#endif

#ifndef ARDUINOJSON_ENABLE_ARDUINO_STRING
#define ARDUINOJSON_ENABLE_ARDUINO_STRING 1
#endif

#include <cstdint>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <string>
#include <sstream>
#include <iostream>
#include <algorithm>
#include <cctype>
#include <vector>

// =============================================================================
// PROGMEM and Flash String Mock Stubs
// =============================================================================
class __FlashStringHelper;
#define PROGMEM
#define PSTR(s) (s)
#define F(s) (reinterpret_cast<const __FlashStringHelper *>(s))

#ifndef pgm_read_byte
#define pgm_read_byte(addr) (*(reinterpret_cast<const uint8_t *>(addr)))
#endif

#ifndef pgm_read_word
#define pgm_read_word(addr) (*(reinterpret_cast<const uint16_t *>(addr)))
#endif

#ifndef pgm_read_dword
#define pgm_read_dword(addr) (*(reinterpret_cast<const uint32_t *>(addr)))
#endif

#ifndef pgm_read_ptr
#define pgm_read_ptr(addr) (*(reinterpret_cast<const void * const *>(addr)))
#endif

// =============================================================================
// Basic Types and Constants
// =============================================================================
typedef bool boolean;
typedef uint8_t byte;

#define HIGH 1
#define LOW  0

#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2

#define DEC 10
#define HEX 16
#define OCT 8
#define BIN 2

// =============================================================================
// Print, Printable, and Stream Classes
// =============================================================================
class Print {
public:
    virtual ~Print() {}
    virtual size_t write(uint8_t) = 0;
    virtual size_t write(const uint8_t *buffer, size_t size) {
        size_t n = 0;
        while (size--) {
            if (write(*buffer++)) n++;
            else break;
        }
        return n;
    }
};

class Printable {
public:
    virtual ~Printable() {}
    virtual size_t printTo(Print& p) const = 0;
};

class Stream : public Print {
public:
    virtual int available() = 0;
    virtual int read() = 0;
    virtual int peek() = 0;
    virtual void flush() {}
    virtual size_t readBytes(char *buffer, size_t length) {
        size_t count = 0;
        while (count < length) {
            int c = read();
            if (c < 0) break;
            *buffer++ = static_cast<char>(c);
            count++;
        }
        return count;
    }
};

// =============================================================================
// Simulated Clock and Timing
// =============================================================================
inline uint64_t g_simulated_millis = 0;

inline uint32_t millis() {
    return static_cast<uint32_t>(g_simulated_millis);
}

inline void delay(uint32_t ms) {
    g_simulated_millis += ms;
}

inline void mockSetMillis(uint32_t ms) {
    g_simulated_millis = ms;
}

inline void mockAdvanceMillis(uint32_t ms) {
    g_simulated_millis += ms;
}

inline void mockResetMillis() {
    g_simulated_millis = 0;
}

// =============================================================================
// Simulated GPIO
// =============================================================================
struct MockPinState {
    uint8_t mode = INPUT;
    uint8_t state = LOW;
    uint32_t writeCount = 0;
};

inline MockPinState g_mockPins[40];

inline void pinMode(uint8_t pin, uint8_t mode) {
    if (pin < 40) {
        g_mockPins[pin].mode = mode;
    }
}

inline void digitalWrite(uint8_t pin, uint8_t val) {
    if (pin < 40) {
        g_mockPins[pin].state = val;
        g_mockPins[pin].writeCount++;
    }
}

inline int digitalRead(uint8_t pin) {
    if (pin < 40) {
        return g_mockPins[pin].state;
    }
    return LOW;
}

inline void mockResetPins() {
    for (int i = 0; i < 40; i++) {
        g_mockPins[i].mode = INPUT;
        g_mockPins[i].state = LOW;
        g_mockPins[i].writeCount = 0;
    }
}

inline uint8_t mockGetPinMode(uint8_t pin) {
    return (pin < 40) ? g_mockPins[pin].mode : INPUT;
}

inline uint8_t mockGetPinState(uint8_t pin) {
    return (pin < 40) ? g_mockPins[pin].state : LOW;
}

inline uint32_t mockGetPinWriteCount(uint8_t pin) {
    return (pin < 40) ? g_mockPins[pin].writeCount : 0;
}

// =============================================================================
// Arduino String Mock Implementation
// =============================================================================
class String {
private:
    std::string _str;

public:
    String() : _str("") {}
    String(const char* cstr) : _str(cstr ? cstr : "") {}
    String(const std::string& s) : _str(s) {}
    String(char c) : _str(1, c) {}
    
    String(int val, int base = 10) {
        char buf[34];
        if (base == 16) snprintf(buf, sizeof(buf), "%X", val);
        else snprintf(buf, sizeof(buf), "%d", val);
        _str = buf;
    }

    String(unsigned int val, int base = 10) {
        char buf[34];
        if (base == 16) snprintf(buf, sizeof(buf), "%X", val);
        else snprintf(buf, sizeof(buf), "%u", val);
        _str = buf;
    }

    String(long val, int base = 10) {
        char buf[34];
        if (base == 16) snprintf(buf, sizeof(buf), "%lX", val);
        else snprintf(buf, sizeof(buf), "%ld", val);
        _str = buf;
    }

    String(unsigned long val, int base = 10) {
        char buf[34];
        if (base == 16) snprintf(buf, sizeof(buf), "%lX", val);
        else snprintf(buf, sizeof(buf), "%lu", val);
        _str = buf;
    }

    String(float val, unsigned char decimalPlaces = 2) {
        char format[16];
        snprintf(format, sizeof(format), "%%.%df", decimalPlaces);
        char buf[34];
        snprintf(buf, sizeof(buf), format, val);
        _str = buf;
    }

    String(double val, unsigned char decimalPlaces = 2) {
        char format[16];
        snprintf(format, sizeof(format), "%%.%df", decimalPlaces);
        char buf[34];
        snprintf(buf, sizeof(buf), format, val);
        _str = buf;
    }

    String(const String& other) : _str(other._str) {}
    String(String&& other) noexcept : _str(std::move(other._str)) {}

    String& operator=(const String& other) {
        if (this != &other) {
            _str = other._str;
        }
        return *this;
    }

    String& operator=(String&& other) noexcept {
        if (this != &other) {
            _str = std::move(other._str);
        }
        return *this;
    }

    String& operator=(const char* cstr) {
        _str = cstr ? cstr : "";
        return *this;
    }

    const char* c_str() const { return _str.c_str(); }
    size_t length() const { return _str.length(); }
    bool isEmpty() const { return _str.empty(); }

    char charAt(size_t index) const {
        if (index < _str.length()) return _str[index];
        return '\0';
    }

    char& operator[](size_t index) {
        return _str[index];
    }

    const char& operator[](size_t index) const {
        return _str[index];
    }

    String substring(size_t from, size_t to = static_cast<size_t>(-1)) const {
        if (from >= _str.length()) return String("");
        if (to > _str.length()) to = _str.length();
        if (to <= from) return String("");
        return String(_str.substr(from, to - from));
    }

    int indexOf(char c, size_t from = 0) const {
        size_t pos = _str.find(c, from);
        return (pos == std::string::npos) ? -1 : static_cast<int>(pos);
    }

    int indexOf(const String& s, size_t from = 0) const {
        size_t pos = _str.find(s.c_str(), from);
        return (pos == std::string::npos) ? -1 : static_cast<int>(pos);
    }

    int lastIndexOf(char c) const {
        size_t pos = _str.rfind(c);
        return (pos == std::string::npos) ? -1 : static_cast<int>(pos);
    }

    bool startsWith(const String& prefix) const {
        if (prefix.length() > _str.length()) return false;
        return _str.compare(0, prefix.length(), prefix.c_str()) == 0;
    }

    bool endsWith(const String& suffix) const {
        if (suffix.length() > _str.length()) return false;
        return _str.compare(_str.length() - suffix.length(), suffix.length(), suffix.c_str()) == 0;
    }

    bool equals(const String& other) const {
        return _str == other._str;
    }

    bool equals(const char* cstr) const {
        return _str == (cstr ? cstr : "");
    }

    bool equalsIgnoreCase(const String& other) const {
        if (_str.length() != other._str.length()) return false;
        for (size_t i = 0; i < _str.length(); i++) {
            if (std::tolower(static_cast<unsigned char>(_str[i])) !=
                std::tolower(static_cast<unsigned char>(other._str[i]))) {
                return false;
            }
        }
        return true;
    }

    void trim() {
        size_t start = 0;
        while (start < _str.length() && std::isspace(static_cast<unsigned char>(_str[start]))) {
            start++;
        }
        size_t end = _str.length();
        while (end > start && std::isspace(static_cast<unsigned char>(_str[end - 1]))) {
            end--;
        }
        _str = _str.substr(start, end - start);
    }

    void toUpperCase() {
        for (char& c : _str) {
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
    }

    void toLowerCase() {
        for (char& c : _str) {
            c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        }
    }

    int toInt() const {
        return std::atoi(_str.c_str());
    }

    float toFloat() const {
        return static_cast<float>(std::atof(_str.c_str()));
    }

    void reserve(size_t size) {
        _str.reserve(size);
    }

    void replace(const String& find, const String& replace) {
        if (find.length() == 0) return;
        size_t pos = 0;
        while ((pos = _str.find(find.c_str(), pos)) != std::string::npos) {
            _str.replace(pos, find.length(), replace.c_str());
            pos += replace.length();
        }
    }

    // Methods needed by ArduinoJson
    bool concat(const char* cstr) {
        if (cstr) _str += cstr;
        return true;
    }

    bool concat(const String& s) {
        _str += s._str;
        return true;
    }

    bool concat(char c) {
        _str += c;
        return true;
    }

    size_t write(uint8_t c) {
        _str.push_back(static_cast<char>(c));
        return 1;
    }

    size_t write(const uint8_t* buffer, size_t size) {
        if (buffer && size > 0) {
            _str.append(reinterpret_cast<const char*>(buffer), size);
            return size;
        }
        return 0;
    }

    // Operators
    String operator+(const String& rhs) const { return String(_str + rhs._str); }
    String operator+(const char* rhs) const { return String(_str + (rhs ? rhs : "")); }
    String operator+(char rhs) const { return String(_str + rhs); }
    String operator+(int rhs) const { return String(_str + std::to_string(rhs)); }

    String& operator+=(const String& rhs) { _str += rhs._str; return *this; }
    String& operator+=(const char* rhs) { if (rhs) _str += rhs; return *this; }
    String& operator+=(char rhs) { _str += rhs; return *this; }

    bool operator==(const String& rhs) const { return _str == rhs._str; }
    bool operator==(const char* rhs) const { return _str == (rhs ? rhs : ""); }
    bool operator!=(const String& rhs) const { return _str != rhs._str; }
    bool operator!=(const char* rhs) const { return _str != (rhs ? rhs : ""); }
    bool operator<(const String& rhs) const { return _str < rhs._str; }

    // Interop with std::string and streams
    const std::string& str() const { return _str; }
};

inline String operator+(const char* lhs, const String& rhs) {
    return String(lhs ? lhs : "") + rhs;
}

inline bool operator==(const char* lhs, const String& rhs) {
    return rhs == lhs;
}

inline bool operator!=(const char* lhs, const String& rhs) {
    return rhs != lhs;
}

inline std::ostream& operator<<(std::ostream& os, const String& s) {
    return os << s.c_str();
}

// =============================================================================
// Simulated Serial Monitor
// =============================================================================
class MockSerial {
private:
    std::string _buffer;
    bool _echoToConsole = false;

public:
    void begin(long) {}

    void setEchoToConsole(bool echo) { _echoToConsole = echo; }

    void print(const String& s) {
        _buffer += s.c_str();
        if (_echoToConsole) std::cout << s.c_str();
    }
    void print(const char* s) {
        if (s) {
            _buffer += s;
            if (_echoToConsole) std::cout << s;
        }
    }
    void print(char c) {
        _buffer += c;
        if (_echoToConsole) std::cout << c;
    }
    void print(int n) {
        std::string s = std::to_string(n);
        _buffer += s;
        if (_echoToConsole) std::cout << s;
    }
    void print(unsigned int n) {
        std::string s = std::to_string(n);
        _buffer += s;
        if (_echoToConsole) std::cout << s;
    }
    void print(long n) {
        std::string s = std::to_string(n);
        _buffer += s;
        if (_echoToConsole) std::cout << s;
    }
    void print(unsigned long n) {
        std::string s = std::to_string(n);
        _buffer += s;
        if (_echoToConsole) std::cout << s;
    }

    void println(const String& s) {
        _buffer += s.c_str();
        _buffer += "\n";
        if (_echoToConsole) std::cout << s.c_str() << std::endl;
    }
    void println(const char* s) {
        if (s) _buffer += s;
        _buffer += "\n";
        if (_echoToConsole) std::cout << (s ? s : "") << std::endl;
    }
    void println(char c) {
        _buffer += c;
        _buffer += "\n";
        if (_echoToConsole) std::cout << c << std::endl;
    }
    void println(int n) {
        std::string s = std::to_string(n);
        _buffer += s + "\n";
        if (_echoToConsole) std::cout << s << std::endl;
    }
    void println() {
        _buffer += "\n";
        if (_echoToConsole) std::cout << std::endl;
    }

    template<typename... Args>
    void printf(const char* format, Args... args) {
        char buf[512];
        snprintf(buf, sizeof(buf), format, args...);
        _buffer += buf;
        if (_echoToConsole) std::cout << buf;
    }

    const std::string& mockGetOutput() const { return _buffer; }
    void mockClearOutput() { _buffer.clear(); }
};

inline MockSerial Serial;

#endif // ARDUINO_H

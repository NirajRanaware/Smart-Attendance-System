/**
 * @file buzzer.h
 * @brief Configurable Acoustic Feedback Controller for ESP32 Smart Attendance System.
 * 
 * Provides clear audio cues for card detection, attendance success, duplicate scans,
 * and error conditions. Supports both active-HIGH and active-LOW buzzer modules.
 */

#ifndef BUZZER_H
#define BUZZER_H

#include <Arduino.h>
#include "config.h"

enum BuzzerPattern {
    BUZZER_OFF,
    BUZZER_BEEP_CARD_DETECTED,    ///< Short click/beep (card tapped)
    BUZZER_BEEP_SUCCESS,          ///< Double upbeat beep (attendance marked Present)
    BUZZER_BEEP_DUPLICATE,        ///< Double medium warning beep (already present)
    BUZZER_BEEP_ERROR,            ///< Triple rapid alert beep (card not found / rejected)
    BUZZER_BEEP_NETWORK_ERROR     ///< Long warning beep (server or Wi-Fi offline)
};

class BuzzerController {
public:
    BuzzerController();

    /**
     * @brief Initializes GPIO pin and sets safe default state.
     */
    void begin();

    /**
     * @brief Plays a specific acoustic pattern.
     * @param pattern The predefined audio cue to sound.
     */
    void play(BuzzerPattern pattern);

    /**
     * @brief Turns on the buzzer according to active level logic.
     */
    void on();

    /**
     * @brief Turns off the buzzer according to active level logic.
     */
    void off();

    /**
     * @brief Single custom pulse with non-blocking or short blocking duration.
     * @param durationMs Milliseconds to keep the buzzer active.
     */
    void pulse(uint16_t durationMs);

private:
    uint8_t _pin;
    bool _enabled;
    bool _activeHigh;

    void rawWrite(bool state);
};

// Global buzzer singleton
extern BuzzerController buzzer;

#endif // BUZZER_H

/**
 * @file buzzer.cpp
 * @brief Implementation of Acoustic Feedback Controller for ESP32.
 */

#include "buzzer.h"

// Global instance
BuzzerController buzzer;

BuzzerController::BuzzerController()
    : _pin(BUZZER_PIN), _enabled(BUZZER_ENABLED), _activeHigh(BUZZER_ACTIVE_HIGH) {
}

void BuzzerController::begin() {
    if (!_enabled) return;

    pinMode(_pin, OUTPUT);
    off(); // Ensure buzzer is silent on boot
    DEBUG_PRINTLN("[BUZZER] Controller initialized on GPIO " + String(_pin));
}

void BuzzerController::rawWrite(bool activeState) {
    if (!_enabled) return;
    
    // Determine electrical logic level based on active configuration
    int level = activeState ? (_activeHigh ? HIGH : LOW) : (_activeHigh ? LOW : HIGH);
    digitalWrite(_pin, level);
}

void BuzzerController::on() {
    rawWrite(true);
}

void BuzzerController::off() {
    rawWrite(false);
}

void BuzzerController::pulse(uint16_t durationMs) {
    if (!_enabled) return;
    on();
    delay(durationMs);
    off();
}

void BuzzerController::play(BuzzerPattern pattern) {
    if (!_enabled) return;

    switch (pattern) {
        case BUZZER_BEEP_CARD_DETECTED:
            // Short 50ms pulse
            on();
            delay(50);
            off();
            break;

        case BUZZER_BEEP_SUCCESS:
            // Double pleasant high beep (80ms on, 60ms off, 100ms on)
            on();
            delay(80);
            off();
            delay(60);
            on();
            delay(100);
            off();
            break;

        case BUZZER_BEEP_DUPLICATE:
            // Double medium warning beep (150ms on, 80ms off, 150ms on)
            on();
            delay(150);
            off();
            delay(80);
            on();
            delay(150);
            off();
            break;

        case BUZZER_BEEP_ERROR:
            // Triple rapid alert beep (60ms on, 50ms off x3)
            for (int i = 0; i < 3; i++) {
                on();
                delay(60);
                off();
                if (i < 2) delay(50);
            }
            break;

        case BUZZER_BEEP_NETWORK_ERROR:
            // Long 400ms warning pulse
            on();
            delay(400);
            off();
            break;

        case BUZZER_OFF:
        default:
            off();
            break;
    }
}

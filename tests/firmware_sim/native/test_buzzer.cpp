/**
 * @file test_buzzer.cpp
 * @brief Comprehensive Unit Tests for BuzzerController (buzzer.cpp).
 * 
 * Tests:
 * 1. Controller initialization (pinMode OUTPUT, boot silence).
 * 2. Idle state verification.
 * 3. Card detected pattern (50ms pulse).
 * 4. Success pattern (80ms on, 60ms off, 100ms on = 240ms).
 * 5. Duplicate pattern (150ms on, 80ms off, 150ms on = 380ms).
 * 6. Error pattern (3 x 60ms on, 50ms off = 280ms).
 * 7. Network error pattern (400ms long warning).
 * 8. Custom pulse duration and final idle state.
 * 9. Active-HIGH electrical logic level verification.
 */

#include "test_framework.h"
#include "Arduino.h"
#include "config.h"
#include "buzzer.h"

// Helper to reset GPIO and simulated time for buzzer testing
static void setupBuzzerTest() {
    mockResetMillis();
    mockResetPins();
    buzzer.begin();
}

// =============================================================================
// PART 5.1: Initialization & Idle State
// =============================================================================

TEST_CASE(Buzzer_InitializationAndIdleState) {
    setupBuzzerTest();

    // Verify GPIO 25 is configured as OUTPUT
    ASSERT_EQ(OUTPUT, mockGetPinMode(BUZZER_PIN));

    // Under Active-HIGH configuration, idle state must be LOW (silent)
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

// =============================================================================
// PART 5.2: Predefined Pattern Timings & Transitions
// =============================================================================

TEST_CASE(Buzzer_PatternCardDetectedTiming) {
    setupBuzzerTest();

    uint32_t startMs = millis();
    uint32_t startWrites = mockGetPinWriteCount(BUZZER_PIN);

    buzzer.play(BUZZER_BEEP_CARD_DETECTED);

    // Short 50ms click
    ASSERT_EQ(startMs + 50u, millis());
    // Must execute ON (HIGH) and OFF (LOW) = 2 transitions
    ASSERT_EQ(startWrites + 2u, mockGetPinWriteCount(BUZZER_PIN));
    // Final state must return to silent LOW
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

TEST_CASE(Buzzer_PatternSuccessTiming) {
    setupBuzzerTest();

    uint32_t startMs = millis();
    uint32_t startWrites = mockGetPinWriteCount(BUZZER_PIN);

    buzzer.play(BUZZER_BEEP_SUCCESS);

    // Double upbeat beep: 80ms ON + 60ms OFF + 100ms ON = 240ms elapsed
    ASSERT_EQ(startMs + 240u, millis());
    // 4 transitions: ON, OFF, ON, OFF
    ASSERT_EQ(startWrites + 4u, mockGetPinWriteCount(BUZZER_PIN));
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

TEST_CASE(Buzzer_PatternDuplicateTiming) {
    setupBuzzerTest();

    uint32_t startMs = millis();
    uint32_t startWrites = mockGetPinWriteCount(BUZZER_PIN);

    buzzer.play(BUZZER_BEEP_DUPLICATE);

    // Double medium warning: 150ms ON + 80ms OFF + 150ms ON = 380ms elapsed
    ASSERT_EQ(startMs + 380u, millis());
    // 4 transitions: ON, OFF, ON, OFF
    ASSERT_EQ(startWrites + 4u, mockGetPinWriteCount(BUZZER_PIN));
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

TEST_CASE(Buzzer_PatternErrorTiming) {
    setupBuzzerTest();

    uint32_t startMs = millis();
    uint32_t startWrites = mockGetPinWriteCount(BUZZER_PIN);

    buzzer.play(BUZZER_BEEP_ERROR);

    // Triple alert: 3 x (60ms ON) + 2 x (50ms pause) = 180 + 100 = 280ms elapsed
    ASSERT_EQ(startMs + 280u, millis());
    // 6 transitions: ON, OFF, ON, OFF, ON, OFF
    ASSERT_EQ(startWrites + 6u, mockGetPinWriteCount(BUZZER_PIN));
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

TEST_CASE(Buzzer_PatternNetworkErrorTiming) {
    setupBuzzerTest();

    uint32_t startMs = millis();
    uint32_t startWrites = mockGetPinWriteCount(BUZZER_PIN);

    buzzer.play(BUZZER_BEEP_NETWORK_ERROR);

    // Long warning pulse: 400ms ON
    ASSERT_EQ(startMs + 400u, millis());
    // 2 transitions: ON, OFF
    ASSERT_EQ(startWrites + 2u, mockGetPinWriteCount(BUZZER_PIN));
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

TEST_CASE(Buzzer_CustomPulse) {
    setupBuzzerTest();

    uint32_t startMs = millis();
    buzzer.pulse(120);

    ASSERT_EQ(startMs + 120u, millis());
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

TEST_CASE(Buzzer_DirectOnOffAndActiveHighLevels) {
    setupBuzzerTest();

    // Verify Active-HIGH logic: on() writes HIGH, off() writes LOW
    buzzer.on();
    ASSERT_EQ(HIGH, mockGetPinState(BUZZER_PIN));

    buzzer.off();
    ASSERT_EQ(LOW, mockGetPinState(BUZZER_PIN));
}

/**
 * @file test_framework.h
 * @brief Lightweight, Standalone Native C++ Assertion and Test Runner Framework.
 * 
 * Provides a zero-dependency header-only testing framework for embedded firmware
 * simulations on host platforms (Windows / Linux).
 */

#ifndef TEST_FRAMEWORK_H
#define TEST_FRAMEWORK_H

#include <iostream>
#include <vector>
#include <string>
#include <sstream>
#include <functional>
#include <chrono>

namespace TestFramework {

    struct TestCase {
        std::string name;
        std::function<void()> func;
    };

    inline std::vector<TestCase>& getTestRegistry() {
        static std::vector<TestCase> registry;
        return registry;
    }

    struct TestStats {
        int totalRun = 0;
        int totalPassed = 0;
        int totalFailed = 0;
        int currentTestFailures = 0;
    };

    inline TestStats g_stats;

    struct AutoRegister {
        AutoRegister(const std::string& name, std::function<void()> func) {
            getTestRegistry().push_back({name, func});
        }
    };

    template<typename T>
    std::string stringify(const T& val) {
        std::ostringstream ss;
        ss << val;
        return ss.str();
    }

    inline void recordFailure(const char* file, int line, const std::string& message) {
        std::cout << "  [FAIL] " << file << ":" << line << " | " << message << std::endl;
        g_stats.currentTestFailures++;
    }

    inline int runAllTests() {
        std::cout << "============================================================" << std::endl;
        std::cout << "   ESP32 SMART ATTENDANCE - HOST FIRMWARE SIMULATION SUITE  " << std::endl;
        std::cout << "============================================================" << std::endl;

        g_stats.totalRun = 0;
        g_stats.totalPassed = 0;
        g_stats.totalFailed = 0;

        auto startTime = std::chrono::steady_clock::now();

        for (const auto& test : getTestRegistry()) {
            g_stats.totalRun++;
            g_stats.currentTestFailures = 0;

            std::cout << "[ RUN      ] " << test.name << std::endl;

            try {
                test.func();
            } catch (const std::exception& ex) {
                recordFailure(__FILE__, __LINE__, std::string("Unhandled exception: ") + ex.what());
            } catch (...) {
                recordFailure(__FILE__, __LINE__, "Unhandled non-std exception occurred");
            }

            if (g_stats.currentTestFailures == 0) {
                std::cout << "[       OK ] " << test.name << std::endl;
                g_stats.totalPassed++;
            } else {
                std::cout << "[  FAILED  ] " << test.name << " (" 
                          << g_stats.currentTestFailures << " assertion failure(s))" << std::endl;
                g_stats.totalFailed++;
            }
        }

        auto endTime = std::chrono::steady_clock::now();
        auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime).count();

        std::cout << "============================================================" << std::endl;
        std::cout << "TEST RESULTS: " 
                  << g_stats.totalRun << " executed | "
                  << g_stats.totalPassed << " passed | "
                  << g_stats.totalFailed << " failed | Time: "
                  << elapsedMs << " ms" << std::endl;
        std::cout << "============================================================" << std::endl;

        return (g_stats.totalFailed == 0) ? 0 : 1;
    }
}

// =============================================================================
// Test Registration & Assertion Macros
// =============================================================================

#define TEST_CASE(name) \
    static void test_func_##name(); \
    static TestFramework::AutoRegister auto_reg_##name(#name, test_func_##name); \
    static void test_func_##name()

#define ASSERT_TRUE(condition) \
    do { \
        if (!(condition)) { \
            TestFramework::recordFailure(__FILE__, __LINE__, \
                std::string("ASSERT_TRUE failed: (") + #condition + ") evaluates to false"); \
        } \
    } while(0)

#define ASSERT_FALSE(condition) \
    do { \
        if (condition) { \
            TestFramework::recordFailure(__FILE__, __LINE__, \
                std::string("ASSERT_FALSE failed: (") + #condition + ") evaluates to true"); \
        } \
    } while(0)

#define ASSERT_EQ(expected, actual) \
    do { \
        auto exp_val = (expected); \
        auto act_val = (actual); \
        if (!(exp_val == act_val)) { \
            TestFramework::recordFailure(__FILE__, __LINE__, \
                std::string("ASSERT_EQ failed: Expected [") + TestFramework::stringify(exp_val) + \
                "] but got [" + TestFramework::stringify(act_val) + "] (" #expected " == " #actual ")"); \
        } \
    } while(0)

#define ASSERT_NE(expected, actual) \
    do { \
        auto exp_val = (expected); \
        auto act_val = (actual); \
        if (exp_val == act_val) { \
            TestFramework::recordFailure(__FILE__, __LINE__, \
                std::string("ASSERT_NE failed: Unexpectedly equal [") + TestFramework::stringify(act_val) + \
                "] (" #expected " != " #actual ")"); \
        } \
    } while(0)

#define ASSERT_STR_EQ(expected, actual) \
    do { \
        std::string exp_str = std::string(expected); \
        std::string act_str = std::string(actual); \
        if (exp_str != act_str) { \
            TestFramework::recordFailure(__FILE__, __LINE__, \
                std::string("ASSERT_STR_EQ failed: Expected [\"") + exp_str + \
                "\"] but got [\"" + act_str + "\"]"); \
        } \
    } while(0)

#define ASSERT_STR_NE(expected, actual) \
    do { \
        std::string exp_str = std::string(expected); \
        std::string act_str = std::string(actual); \
        if (exp_str == act_str) { \
            TestFramework::recordFailure(__FILE__, __LINE__, \
                std::string("ASSERT_STR_NE failed: Strings unexpectedly match [\"") + act_str + "\"]"); \
        } \
    } while(0)

#endif // TEST_FRAMEWORK_H

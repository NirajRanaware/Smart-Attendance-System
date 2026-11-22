/**
 * @file test_api_client.cpp
 * @brief Comprehensive Unit Tests for AttendanceApiClient (api_client.cpp).
 * 
 * Tests:
 * 1. Request construction (URL, method, JSON body, headers, timeout).
 * 2. HTTP response matrix (201, 401, 403, 404, 409, 500, 503, <=0).
 * 3. JSON error handling (empty body, malformed JSON, missing objects, invalid root).
 * 4. Hardware authentication pre-checks (empty ID, empty key, valid key).
 * 5. Wi-Fi pre-check (disconnection guard before HTTP).
 * 6. http.end() session termination verification across paths.
 */

#include "test_framework.h"
#include "Arduino.h"
#include "WiFi.h"
#include "WiFiClient.h"
#include "HTTPClient.h"
#include <ArduinoJson.h>
#include "config.h"
#include "api_client.h"

// Helper to ensure clean API client test state
static void setupApiTest() {
    mockResetMillis();
    mockHttpReset();
    mockResetDeviceCredentials();
    WiFi.mockReset();
    WiFi.mockSetStatus(WL_CONNECTED); // Connected by default for API tests
}

// =============================================================================
// PART 2.1: Request Construction & Transport Configuration
// =============================================================================

TEST_CASE(API_RequestConstructionHeadersAndPayload) {
    setupApiTest();
    mockHttpSetResponse(201, "{\"scan_result\":\"SUCCESS\",\"student\":{\"roll_number\":\"2024-CS-001\",\"student_name\":\"Alice\"}}");

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SUCCESS, resp.outcome);
    ASSERT_EQ(1, mockHttpGetPostCount());

    // Verify endpoint URL
    ASSERT_STR_EQ("http://192.168.1.100:5000/api/scans", mockHttpGetLastUrl().c_str());

    // Verify headers
    ASSERT_STR_EQ("application/json", mockHttpGetHeader("Content-Type").c_str());
    ASSERT_STR_EQ("application/json", mockHttpGetHeader("Accept").c_str());
    ASSERT_STR_EQ("ESP32_ATTENDANCE_01", mockHttpGetHeader("X-Device-Id").c_str());
    ASSERT_STR_EQ("d3v1c3_s3cr3t_k3y_32_ch4r4ct3rs_!", mockHttpGetHeader("X-API-Key").c_str());

    // Verify payload JSON structure
    std::string postBody = mockHttpGetLastBody();
    JsonDocument reqDoc;
    DeserializationError err = deserializeJson(reqDoc, postBody.c_str());
    ASSERT_FALSE(err);
    ASSERT_STR_EQ("1A2B3C4D", reqDoc["card_uid"].as<String>().c_str());
    ASSERT_STR_EQ("ESP32_ATTENDANCE_01", reqDoc["source"].as<String>().c_str());

    // Verify session cleanup
    ASSERT_TRUE(mockHttpWasEndCalled());
}

TEST_CASE(API_EndpointUrlBuilder) {
    setupApiTest();
    String endpoint = apiClient.getScanEndpointUrl();
    ASSERT_STR_EQ("http://192.168.1.100:5000/api/scans", endpoint.c_str());
}

TEST_CASE(API_WifiDisconnectedPreCheck) {
    setupApiTest();
    // Simulate Wi-Fi dropped prior to scan
    WiFi.mockSetStatus(WL_DISCONNECTED);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_NETWORK_ERROR, resp.outcome);
    ASSERT_EQ(-1, resp.httpCode);
    ASSERT_STR_EQ("Wi-Fi disconnected", resp.message.c_str());
    // Zero HTTP requests sent when Wi-Fi is disconnected
    ASSERT_EQ(0, mockHttpGetPostCount());
}

TEST_CASE(API_HttpBeginFailureHandling) {
    setupApiTest();
    mockHttpSetBeginSuccess(false);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_NETWORK_ERROR, resp.outcome);
    ASSERT_EQ(-1, resp.httpCode);
    ASSERT_STR_EQ("Connection Failed", resp.error.c_str());
}

// =============================================================================
// PART 2.2: HTTP Response Matrix Verification
// =============================================================================

TEST_CASE(API_Http201_SuccessfulAttendance) {
    setupApiTest();
    const char* json201 = 
        "{\"scan_result\":\"SUCCESS\","
        "\"message\":\"Attendance marked successfully\","
        "\"student\":{\"roll_number\":\"2024-CS-042\",\"student_name\":\"Bruce Wayne\"},"
        "\"attendance\":{\"status\":\"Present\"}}";
    mockHttpSetResponse(201, json201);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SUCCESS, resp.outcome);
    ASSERT_EQ(201, resp.httpCode);
    ASSERT_STR_EQ("SUCCESS", resp.scanResult.c_str());
    ASSERT_STR_EQ("2024-CS-042", resp.rollNumber.c_str());
    ASSERT_STR_EQ("Bruce Wayne", resp.studentName.c_str());
    ASSERT_STR_EQ("Present", resp.attendanceStatus.c_str());
    ASSERT_TRUE(mockHttpWasEndCalled());
}

TEST_CASE(API_Http201_CustomAttendanceStatusLate) {
    setupApiTest();
    const char* jsonLate = 
        "{\"scan_result\":\"SUCCESS\","
        "\"student\":{\"roll_number\":\"2024-CS-010\",\"student_name\":\"Clark Kent\"},"
        "\"attendance\":{\"status\":\"Late\"}}";
    mockHttpSetResponse(201, jsonLate);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SUCCESS, resp.outcome);
    ASSERT_STR_EQ("Late", resp.attendanceStatus.c_str());
}

TEST_CASE(API_Http201_FallbackAttendanceStatusWhenOmitted) {
    setupApiTest();
    // Server returns student object but omits attendance object and status field
    const char* jsonNoStatus = 
        "{\"scan_result\":\"SUCCESS\","
        "\"student\":{\"roll_number\":\"2024-CS-015\",\"student_name\":\"Diana Prince\"}}";
    mockHttpSetResponse(201, jsonNoStatus);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SUCCESS, resp.outcome);
    // Safe default fallback "Present"
    ASSERT_STR_EQ("Present", resp.attendanceStatus.c_str());
}

TEST_CASE(API_Http401_DeviceAuthenticationFailure) {
    setupApiTest();
    // Case 1: Backend returns explicit error field "Unauthorized"
    const char* json401 = 
        "{\"error\":\"Unauthorized\","
        "\"message\":\"Device authentication failed. Invalid API key.\"}";
    mockHttpSetResponse(401, json401);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    // SCAN_OUTCOME_DEVICE_AUTH_ERROR (aliased to SCAN_OUTCOME_SERVER_ERROR)
    ASSERT_EQ(SCAN_OUTCOME_DEVICE_AUTH_ERROR, resp.outcome);
    ASSERT_EQ(401, resp.httpCode);
    ASSERT_STR_EQ("Unauthorized", resp.error.c_str());
    ASSERT_TRUE(mockHttpWasEndCalled());

    // Case 2: Backend response omits error field; firmware defaults to "Device Auth Error"
    const char* json401NoError = "{\"message\":\"Invalid credentials\"}";
    mockHttpSetResponse(401, json401NoError);

    ScanResponse resp2 = apiClient.sendScan("1A2B3C4D");
    ASSERT_EQ(SCAN_OUTCOME_DEVICE_AUTH_ERROR, resp2.outcome);
    ASSERT_EQ(401, resp2.httpCode);
    ASSERT_STR_EQ("Device Auth Error", resp2.error.c_str());
}

TEST_CASE(API_Http403_InactiveStudentAccount) {
    setupApiTest();
    const char* json403Inactive = 
        "{\"scan_result\":\"INACTIVE_STUDENT\","
        "\"error\":\"Forbidden\","
        "\"message\":\"Student account is deactivated\"}";
    mockHttpSetResponse(403, json403Inactive);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_INACTIVE_STUDENT, resp.outcome);
    ASSERT_EQ(403, resp.httpCode);
    ASSERT_STR_EQ("INACTIVE_STUDENT", resp.scanResult.c_str());
}

TEST_CASE(API_Http403_GenericForbiddenMappedToServerError) {
    setupApiTest();
    // HTTP 403 without INACTIVE_STUDENT scan_result
    const char* json403Generic = 
        "{\"error\":\"Forbidden\","
        "\"message\":\"Access denied to resource\"}";
    mockHttpSetResponse(403, json403Generic);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    // Must safely map to SCAN_OUTCOME_SERVER_ERROR
    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_EQ(403, resp.httpCode);
}

TEST_CASE(API_Http404_UnknownCard) {
    setupApiTest();
    const char* json404 = 
        "{\"scan_result\":\"UNKNOWN_CARD\","
        "\"error\":\"Not Found\","
        "\"message\":\"RFID card UID is not registered\"}";
    mockHttpSetResponse(404, json404);

    ScanResponse resp = apiClient.sendScan("DEADBEEF");

    ASSERT_EQ(SCAN_OUTCOME_UNKNOWN_CARD, resp.outcome);
    ASSERT_EQ(404, resp.httpCode);
    ASSERT_STR_EQ("UNKNOWN_CARD", resp.scanResult.c_str());
}

TEST_CASE(API_Http409_DuplicateScan) {
    setupApiTest();
    const char* json409Dup = 
        "{\"scan_result\":\"DUPLICATE\","
        "\"message\":\"Student already marked present in this session\","
        "\"student\":{\"roll_number\":\"2024-CS-001\",\"student_name\":\"Barry Allen\"},"
        "\"attendance\":{\"status\":\"ALREADY PRESENT\"}}";
    mockHttpSetResponse(409, json409Dup);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_DUPLICATE, resp.outcome);
    ASSERT_EQ(409, resp.httpCode);
    ASSERT_STR_EQ("DUPLICATE", resp.scanResult.c_str());
    ASSERT_STR_EQ("2024-CS-001", resp.rollNumber.c_str());
    ASSERT_STR_EQ("Barry Allen", resp.studentName.c_str());
    ASSERT_STR_EQ("ALREADY PRESENT", resp.attendanceStatus.c_str());
}

TEST_CASE(API_Http409_SessionExpired) {
    setupApiTest();
    const char* json409Expired = 
        "{\"scan_result\":\"SESSION_EXPIRED\","
        "\"error\":\"Conflict\","
        "\"message\":\"The lecture session has expired\"}";
    mockHttpSetResponse(409, json409Expired);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SESSION_EXPIRED, resp.outcome);
    ASSERT_EQ(409, resp.httpCode);
    ASSERT_STR_EQ("SESSION_EXPIRED", resp.scanResult.c_str());
}

TEST_CASE(API_Http409_NoActiveSession) {
    setupApiTest();
    const char* json409NoSession = 
        "{\"scan_result\":\"NO_ACTIVE_SESSION\","
        "\"error\":\"Conflict\","
        "\"message\":\"No class session is currently active\"}";
    mockHttpSetResponse(409, json409NoSession);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_NO_SESSION, resp.outcome);
    ASSERT_EQ(409, resp.httpCode);
    ASSERT_STR_EQ("NO_ACTIVE_SESSION", resp.scanResult.c_str());
}

TEST_CASE(API_Http409_UnknownScanResultNeverAssumesNoSession) {
    setupApiTest();
    // HTTP 409 with unrecognized scan_result string
    const char* json409Unknown = 
        "{\"scan_result\":\"FUTURE_CONFLICT_CODE\","
        "\"error\":\"Conflict\","
        "\"message\":\"Some conflict condition\"}";
    mockHttpSetResponse(409, json409Unknown);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    // Stage 6 correction rule: Unknown HTTP 409 must map to SCAN_OUTCOME_SERVER_ERROR
    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_NE(SCAN_OUTCOME_NO_SESSION, resp.outcome);
}

TEST_CASE(API_Http409_MissingScanResultNeverAssumesNoSession) {
    setupApiTest();
    // HTTP 409 with completely missing scan_result field
    const char* json409Missing = 
        "{\"error\":\"Conflict\","
        "\"message\":\"Conflict without scan_result\"}";
    mockHttpSetResponse(409, json409Missing);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    // Stage 6 correction rule: Missing scan_result on 409 must map to SERVER_ERROR
    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_NE(SCAN_OUTCOME_NO_SESSION, resp.outcome);
}

TEST_CASE(API_Http500_InternalServerError) {
    setupApiTest();
    const char* json500 = 
        "{\"error\":\"Internal Server Error\","
        "\"message\":\"Database disk I/O error\"}";
    mockHttpSetResponse(500, json500);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_EQ(500, resp.httpCode);
}

TEST_CASE(API_Http503_ServiceUnavailable) {
    setupApiTest();
    const char* json503 = 
        "{\"error\":\"Service Unavailable\","
        "\"message\":\"Server overloaded\"}";
    mockHttpSetResponse(503, json503);

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_EQ(503, resp.httpCode);
}

TEST_CASE(API_NetworkFailureNegativeHttpCode) {
    setupApiTest();
    // HTTPClient returns -1 (connection refused)
    mockHttpSetResponse(-1, "");

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_NETWORK_ERROR, resp.outcome);
    ASSERT_EQ(-1, resp.httpCode);
    ASSERT_STR_EQ("Connection Failed", resp.error.c_str());
    ASSERT_TRUE(mockHttpWasEndCalled());
}

// =============================================================================
// PART 2.3: Malformed & Pathological JSON Handling
// =============================================================================

TEST_CASE(API_EmptyResponseBody) {
    setupApiTest();
    mockHttpSetResponse(200, "   "); // Empty / whitespace body

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_STR_EQ("Empty Response", resp.error.c_str());
    ASSERT_TRUE(mockHttpWasEndCalled());
}

TEST_CASE(API_MalformedJsonSyntax) {
    setupApiTest();
    mockHttpSetResponse(200, "{ invalid_json: unquoted, }");

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_STR_EQ("JSON Parse Error", resp.error.c_str());
}

TEST_CASE(API_JsonArrayRootInsteadOfObject) {
    setupApiTest();
    // JSON root is an array instead of an object
    mockHttpSetResponse(200, "[\"item1\", \"item2\"]");

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SERVER_ERROR, resp.outcome);
    ASSERT_STR_EQ("Invalid Response", resp.error.c_str());
}

TEST_CASE(API_MissingStudentAndAttendanceObjectsIn201) {
    setupApiTest();
    // 201 response with no student or attendance objects
    mockHttpSetResponse(201, "{\"scan_result\":\"SUCCESS\",\"message\":\"Marked\"}");

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_SUCCESS, resp.outcome);
    // Student identity gracefully defaults to "--"
    ASSERT_STR_EQ("--", resp.rollNumber.c_str());
    ASSERT_STR_EQ("--", resp.studentName.c_str());
    // Attendance status defaults to "Present"
    ASSERT_STR_EQ("Present", resp.attendanceStatus.c_str());
}

// =============================================================================
// PART 2.4: Hardware Device Authentication Pre-Checks
// =============================================================================

TEST_CASE(API_AuthPreCheck_EmptyDeviceIdAbortsLocally) {
    setupApiTest();
    g_mock_device_id = ""; // Empty DEVICE_ID in firmware config

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_DEVICE_AUTH_ERROR, resp.outcome);
    ASSERT_EQ(401, resp.httpCode);
    ASSERT_STR_EQ("DEVICE_AUTH_ERROR", resp.scanResult.c_str());
    // Zero network requests initiated
    ASSERT_EQ(0, mockHttpGetPostCount());
}

TEST_CASE(API_AuthPreCheck_EmptyDeviceApiKeyAbortsLocally) {
    setupApiTest();
    g_mock_device_api_key = ""; // Empty DEVICE_API_KEY in firmware config

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    ASSERT_EQ(SCAN_OUTCOME_DEVICE_AUTH_ERROR, resp.outcome);
    ASSERT_EQ(401, resp.httpCode);
    ASSERT_STR_EQ("DEVICE_AUTH_ERROR", resp.scanResult.c_str());
    // Zero network requests initiated
    ASSERT_EQ(0, mockHttpGetPostCount());
}

TEST_CASE(API_AuthPreCheck_ShortApiKeyTransmittedAndRejectedByBackend) {
    setupApiTest();
    // Key is non-empty but shorter than 32 chars (e.g. 10 chars)
    g_mock_device_api_key = "short_key!";
    mockHttpSetResponse(401, "{\"error\":\"Unauthorized\",\"message\":\"Key too short\"}");

    ScanResponse resp = apiClient.sendScan("1A2B3C4D");

    // Per production code: strlen(DEVICE_API_KEY) > 0 proceeds to transport,
    // and backend rejects with HTTP 401 which maps to SCAN_OUTCOME_DEVICE_AUTH_ERROR
    ASSERT_EQ(1, mockHttpGetPostCount());
    ASSERT_STR_EQ("short_key!", mockHttpGetHeader("X-API-Key").c_str());
    ASSERT_EQ(SCAN_OUTCOME_DEVICE_AUTH_ERROR, resp.outcome);
    ASSERT_EQ(401, resp.httpCode);
}

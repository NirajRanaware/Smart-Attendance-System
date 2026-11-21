/**
 * @file api_client.cpp
 * @brief Implementation of REST API Client for Smart Attendance System.
 */

#include "api_client.h"

// Global API client instance
AttendanceApiClient apiClient;

AttendanceApiClient::AttendanceApiClient() {
}

String AttendanceApiClient::maskUid(const String& uid) {
    if (uid.length() <= 4) {
        return "****";
    }
    return uid.substring(0, 2) + "****" + uid.substring(uid.length() - 2);
}

String AttendanceApiClient::getScanEndpointUrl() {
    return "http://" + String(SERVER_HOST) + ":" + String(SERVER_PORT) + String(API_SCAN_ENDPOINT);
}

ScanResponse AttendanceApiClient::sendScan(const String& cardUid) {
    ScanResponse resp;
    resp.outcome = SCAN_OUTCOME_NETWORK_ERROR;
    resp.httpCode = -1;
    resp.scanResult = "";
    resp.rollNumber = "--";
    resp.studentName = "--";
    resp.attendanceStatus = "--";
    resp.message = "";
    resp.error = "";

    // 1. Verify local device credentials configuration before any network activity
    if (strlen(DEVICE_ID) == 0 || strlen(DEVICE_API_KEY) == 0) {
        DEBUG_PRINTLN("[API] ERROR: Device authentication credentials missing or empty in config.h.");
        DEBUG_PRINTLN("[API] Both DEVICE_ID and DEVICE_API_KEY must be configured.");
        resp.outcome = SCAN_OUTCOME_DEVICE_AUTH_ERROR;
        resp.httpCode = 401;
        resp.scanResult = "DEVICE_AUTH_ERROR";
        resp.error = "Device Auth Error";
        resp.message = "Missing DEVICE_ID or DEVICE_API_KEY in firmware config";
        return resp;
    }

    // 2. Verify Wi-Fi connectivity before attempting HTTP
    if (WiFi.status() != WL_CONNECTED) {
        DEBUG_PRINTLN("[API] ERROR: Cannot send scan. Wi-Fi is disconnected.");
        resp.outcome = SCAN_OUTCOME_NETWORK_ERROR;
        resp.httpCode = -1;
        resp.error = "Network Error";
        resp.message = "Wi-Fi disconnected";
        return resp;
    }

    // 3. Prepare JSON payload using ArduinoJson v7
    JsonDocument reqDoc;
    reqDoc["card_uid"] = cardUid;
    reqDoc["source"] = DEVICE_ID;

    String requestBody;
    serializeJson(reqDoc, requestBody);

    String endpointUrl = getScanEndpointUrl();
    DEBUG_PRINTLN("[API] POST -> " + endpointUrl);
    DEBUG_PRINTLN("[API] Scan UID: " + maskUid(cardUid) + " | Device: " + String(DEVICE_ID));

    // 4. Configure HTTPClient
    HTTPClient http;
    if (!http.begin(_wifiClient, endpointUrl)) {
        DEBUG_PRINTLN("[API] ERROR: HTTPClient failed to initialize connection to endpoint.");
        resp.outcome = SCAN_OUTCOME_NETWORK_ERROR;
        resp.httpCode = -1;
        resp.error = "Connection Failed";
        resp.message = "Failed to initialize HTTP client";
        return resp;
    }

    // Add standard and device authentication headers
    http.addHeader("Content-Type", "application/json");
    http.addHeader("Accept", "application/json");
    http.addHeader("X-Device-Id", DEVICE_ID);
    http.addHeader("X-API-Key", DEVICE_API_KEY);
    http.setTimeout(HTTP_REQUEST_TIMEOUT);

    // 5. Send HTTP POST request
    int httpCode = http.POST(requestBody);
    resp.httpCode = httpCode;

    // Handle connection or transport failure (code <= 0)
    if (httpCode <= 0) {
        DEBUG_PRINTF("[API] ERROR: HTTP request failed, code: %d (%s)\n",
                     httpCode, http.errorToString(httpCode).c_str());
        resp.outcome = SCAN_OUTCOME_NETWORK_ERROR;
        resp.error = "Connection Failed";
        resp.message = http.errorToString(httpCode);
        http.end();
        return resp;
    }

    DEBUG_PRINTF("[API] Response Code: %d\n", httpCode);

    // 6. Retrieve response payload
    String responseString = http.getString();
    http.end(); // Safely close HTTP session immediately after reading response

    responseString.trim();
    if (responseString.length() == 0) {
        DEBUG_PRINTLN("[API] ERROR: Server returned empty response body.");
        resp.outcome = (httpCode == 401) ? SCAN_OUTCOME_DEVICE_AUTH_ERROR : SCAN_OUTCOME_SERVER_ERROR;
        resp.error = (httpCode == 401) ? "Device Auth Error" : "Empty Response";
        resp.message = "Server returned empty response body";
        return resp;
    }

    // 7. Parse JSON response body
    JsonDocument resDoc;
    DeserializationError jsonErr = deserializeJson(resDoc, responseString);
    if (jsonErr) {
        DEBUG_PRINTF("[API] ERROR: JSON deserialization failed: %s\n", jsonErr.c_str());
        resp.outcome = (httpCode == 401) ? SCAN_OUTCOME_DEVICE_AUTH_ERROR : SCAN_OUTCOME_SERVER_ERROR;
        resp.error = "JSON Parse Error";
        resp.message = String("JSON parse error: ") + jsonErr.c_str();
        return resp;
    }

    if (!resDoc.is<JsonObject>()) {
        DEBUG_PRINTLN("[API] ERROR: Response root is not a JSON object.");
        resp.outcome = (httpCode == 401) ? SCAN_OUTCOME_DEVICE_AUTH_ERROR : SCAN_OUTCOME_SERVER_ERROR;
        resp.error = "Invalid Response";
        resp.message = "Expected JSON object at root";
        return resp;
    }

    // 8. Safely extract common response fields
    if (resDoc["scan_result"].is<const char*>()) {
        resp.scanResult = resDoc["scan_result"].as<String>();
    }
    if (resDoc["message"].is<const char*>()) {
        resp.message = resDoc["message"].as<String>();
    }
    if (resDoc["error"].is<const char*>()) {
        resp.error = resDoc["error"].as<String>();
    }

    // 9. Classify outcome based on HTTP code and machine-readable fields
    if (httpCode == 201) {
        // HTTP 201: Attendance Successfully Marked
        resp.outcome = SCAN_OUTCOME_SUCCESS;
        resp.attendanceStatus = "PRESENT";

        if (resDoc["student"].is<JsonObject>()) {
            JsonObjectConst studentObj = resDoc["student"];
            if (studentObj["roll_number"].is<const char*>()) {
                resp.rollNumber = studentObj["roll_number"].as<String>();
            }
            if (studentObj["student_name"].is<const char*>()) {
                resp.studentName = studentObj["student_name"].as<String>();
            }
        } else if (resDoc["attendance"].is<JsonObject>()) {
            JsonObjectConst attObj = resDoc["attendance"];
            if (attObj["roll_number"].is<const char*>()) {
                resp.rollNumber = attObj["roll_number"].as<String>();
            }
            if (attObj["student_name"].is<const char*>()) {
                resp.studentName = attObj["student_name"].as<String>();
            }
            if (attObj["status"].is<const char*>()) {
                resp.attendanceStatus = attObj["status"].as<String>();
            }
        }

    } else if (httpCode == 409) {
        // HTTP 409: Conflict (Duplicate, No Active Session, or Session Expired)
        if (resp.scanResult.equalsIgnoreCase("DUPLICATE")) {
            resp.outcome = SCAN_OUTCOME_DUPLICATE;
            resp.attendanceStatus = "ALREADY PRESENT";

            if (resDoc["student"].is<JsonObject>()) {
                JsonObjectConst studentObj = resDoc["student"];
                if (studentObj["roll_number"].is<const char*>()) {
                    resp.rollNumber = studentObj["roll_number"].as<String>();
                }
                if (studentObj["student_name"].is<const char*>()) {
                    resp.studentName = studentObj["student_name"].as<String>();
                }
            } else if (resDoc["attendance"].is<JsonObject>()) {
                JsonObjectConst attObj = resDoc["attendance"];
                if (attObj["roll_number"].is<const char*>()) {
                    resp.rollNumber = attObj["roll_number"].as<String>();
                }
                if (attObj["student_name"].is<const char*>()) {
                    resp.studentName = attObj["student_name"].as<String>();
                }
            }
        } else if (resp.scanResult.equalsIgnoreCase("SESSION_EXPIRED")) {
            resp.outcome = SCAN_OUTCOME_SESSION_EXPIRED;
            resp.rollNumber = "--";
            resp.studentName = "--";
            resp.attendanceStatus = "--";
        } else if (resp.scanResult.equalsIgnoreCase("NO_ACTIVE_SESSION")) {
            resp.outcome = SCAN_OUTCOME_NO_SESSION;
            resp.rollNumber = "--";
            resp.studentName = "--";
            resp.attendanceStatus = "--";
        } else {
            // Fallback for unexpected or missing scan_result
            if (resp.error.equalsIgnoreCase("Duplicate Attendance")) {
                resp.outcome = SCAN_OUTCOME_DUPLICATE;
                resp.attendanceStatus = "ALREADY PRESENT";
            } else if (resp.error.equalsIgnoreCase("No Active Session")) {
                resp.outcome = SCAN_OUTCOME_NO_SESSION;
            } else {
                resp.outcome = SCAN_OUTCOME_NO_SESSION;
            }
            resp.rollNumber = "--";
            resp.studentName = "--";
            resp.attendanceStatus = "--";
        }

    } else if (httpCode == 404) {
        // HTTP 404: Unknown RFID Card UID
        resp.outcome = SCAN_OUTCOME_UNKNOWN_CARD;
        resp.rollNumber = "--";
        resp.studentName = "--";
        resp.attendanceStatus = "--";

    } else if (httpCode == 403) {
        // HTTP 403: Inactive Student Account (or endpoint forbidden)
        if (resp.scanResult.equalsIgnoreCase("INACTIVE_STUDENT") ||
            resp.error.equalsIgnoreCase("Inactive Student")) {
            resp.outcome = SCAN_OUTCOME_INACTIVE_STUDENT;
        } else {
            resp.outcome = SCAN_OUTCOME_SERVER_ERROR;
            if (resp.error.length() == 0) resp.error = "Forbidden";
        }
        resp.rollNumber = "--";
        resp.studentName = "--";
        resp.attendanceStatus = "--";

    } else if (httpCode == 401) {
        // HTTP 401: Device Authentication Failure
        DEBUG_PRINTLN("[API] ERROR: Device Authentication Failed (HTTP 401). Invalid or unauthorized device credentials.");
        resp.outcome = SCAN_OUTCOME_DEVICE_AUTH_ERROR;
        if (resp.error.length() == 0) {
            resp.error = "Device Auth Error";
        }
        resp.rollNumber = "--";
        resp.studentName = "--";
        resp.attendanceStatus = "--";

    } else {
        // Other HTTP 4xx / 5xx error
        DEBUG_PRINTF("[API] ERROR: Server error response (HTTP %d): %s\n", httpCode, resp.message.c_str());
        resp.outcome = SCAN_OUTCOME_SERVER_ERROR;
        if (resp.error.length() == 0) {
            resp.error = "HTTP " + String(httpCode);
        }
        resp.rollNumber = "--";
        resp.studentName = "--";
        resp.attendanceStatus = "--";
    }

    return resp;
}

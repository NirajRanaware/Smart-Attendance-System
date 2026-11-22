/**
 * @file api_client.h
 * @brief REST API Client for Smart Attendance Flask Backend.
 * 
 * Formats scan payloads, sends HTTP POST requests to /api/scans,
 * deserializes JSON responses using ArduinoJson, and maps status codes
 * to high-level attendance outcome structures.
 */

#ifndef API_CLIENT_H
#define API_CLIENT_H

#include <Arduino.h>
#include <WiFiClient.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

enum ScanOutcome {
    SCAN_OUTCOME_SUCCESS,           ///< HTTP 201: Fresh attendance marked Present
    SCAN_OUTCOME_DUPLICATE,         ///< HTTP 409: Student already marked Present in this session
    SCAN_OUTCOME_UNKNOWN_CARD,      ///< HTTP 404: Card UID not registered in SQLite
    SCAN_OUTCOME_INACTIVE_STUDENT,  ///< HTTP 403: Student account is deactivated
    SCAN_OUTCOME_NO_SESSION,        ///< HTTP 409: No class session is currently active
    SCAN_OUTCOME_SESSION_EXPIRED,   ///< HTTP 409: Active session reached end_time
    SCAN_OUTCOME_SERVER_ERROR,      ///< HTTP 500, HTTP 400 validation error, or unclassified 409
    SCAN_OUTCOME_NETWORK_ERROR,     ///< Wi-Fi disconnected or HTTPClient connection failed
    SCAN_OUTCOME_DEVICE_AUTH_ERROR = SCAN_OUTCOME_SERVER_ERROR ///< HTTP 401 or missing credentials: Authentication error
};

struct ScanResponse {
    ScanOutcome outcome;
    int httpCode;
    String scanResult;          ///< Raw scan_result string from server ("SUCCESS", "DUPLICATE", etc.)
    String rollNumber;          ///< Identified student roll number from backend SQLite
    String studentName;         ///< Registered student name from backend SQLite
    String attendanceStatus;    ///< Status string ("Present", "ALREADY PRESENT", etc.)
    String message;             ///< Backend response message
    String error;               ///< Backend error label
};

class AttendanceApiClient {
public:
    AttendanceApiClient();

    /**
     * @brief Sends an RFID scan event to the Flask backend.
     * 
     * Target: POST http://<SERVER_HOST>:<SERVER_PORT>/api/scans
     * Payload: {"card_uid": "<uid>", "source": "<device_id>"}
     * Headers: Content-Type, Accept, X-Device-Id, X-API-Key
     * 
     * @param cardUid Normalized uppercase hexadecimal card UID.
     * @return ScanResponse Structured result containing outcome and student details.
     */
    ScanResponse sendScan(const String& cardUid);

    /**
     * @brief Builds the target URL for the scan endpoint.
     */
    String getScanEndpointUrl();

private:
    WiFiClient _wifiClient;

    /**
     * @brief Masks card UID for privacy-safe serial debugging.
     */
    static String maskUid(const String& uid);
};

// Global API client singleton
extern AttendanceApiClient apiClient;

#endif // API_CLIENT_H

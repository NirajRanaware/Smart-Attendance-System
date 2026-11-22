/**
 * @file HTTPClient.h
 * @brief Host-side Mock and Test-only Live Transport for ESP32 HTTPClient.
 */

#ifndef HTTP_CLIENT_H
#define HTTP_CLIENT_H

#pragma push_macro("INPUT")
#pragma push_macro("OUTPUT")
#undef INPUT
#undef OUTPUT

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>

#pragma pop_macro("OUTPUT")
#pragma pop_macro("INPUT")

#include "Arduino.h"
#include "WiFi.h"
#include "WiFiClient.h"
#include <map>
#include <vector>
#include <string>
#include <sstream>

// HTTP status codes
#define HTTP_CODE_OK                    200
#define HTTP_CODE_CREATED               201
#define HTTP_CODE_BAD_REQUEST           400
#define HTTP_CODE_UNAUTHORIZED          401
#define HTTP_CODE_FORBIDDEN             403
#define HTTP_CODE_NOT_FOUND             404
#define HTTP_CODE_CONFLICT              409
#define HTTP_CODE_INTERNAL_SERVER_ERROR 500

// HTTP error codes (< 0)
#define HTTPC_ERROR_CONNECTION_REFUSED  (-1)
#define HTTPC_ERROR_SEND_HEADER_FAILED  (-2)
#define HTTPC_ERROR_SEND_PAYLOAD_FAILED (-3)
#define HTTPC_ERROR_NOT_CONNECTED       (-4)
#define HTTPC_ERROR_CONNECTION_LOST     (-5)
#define HTTPC_ERROR_NO_HTTP_SERVER      (-6)
#define HTTPC_ERROR_TOO_LESS_RAM        (-7)
#define HTTPC_ERROR_ENCODING            (-8)
#define HTTPC_ERROR_STREAM_WRITE        (-9)
#define HTTPC_ERROR_READ_TIMEOUT        (-10)

struct MockHttpState {
    int responseCode = 200;
    std::string responseBody = "{}";
    bool beginSuccess = true;
    std::string lastUrl = "";
    std::string lastRequestBody = "";
    std::map<std::string, std::string> lastHeaders;
    bool wasEndCalled = false;
    int postCallCount = 0;
    uint32_t timeoutMs = 5000;
    bool useLiveTransport = false; // When true, sends real HTTP requests over localhost sockets
};

inline MockHttpState g_mockHttp;

inline void mockHttpSetResponse(int code, const String& body) {
    g_mockHttp.responseCode = code;
    g_mockHttp.responseBody = body.c_str();
}

inline void mockHttpSetBeginSuccess(bool success) {
    g_mockHttp.beginSuccess = success;
}

inline void mockHttpSetLiveTransport(bool enable) {
    g_mockHttp.useLiveTransport = enable;
}

inline const std::string& mockHttpGetLastUrl() {
    return g_mockHttp.lastUrl;
}

inline const std::string& mockHttpGetLastBody() {
    return g_mockHttp.lastRequestBody;
}

inline std::string mockHttpGetHeader(const std::string& name) {
    auto it = g_mockHttp.lastHeaders.find(name);
    if (it != g_mockHttp.lastHeaders.end()) return it->second;
    return "";
}

inline bool mockHttpWasEndCalled() {
    return g_mockHttp.wasEndCalled;
}

inline int mockHttpGetPostCount() {
    return g_mockHttp.postCallCount;
}

inline void mockHttpReset() {
    g_mockHttp.responseCode = 200;
    g_mockHttp.responseBody = "{}";
    g_mockHttp.beginSuccess = true;
    g_mockHttp.lastUrl = "";
    g_mockHttp.lastRequestBody = "";
    g_mockHttp.lastHeaders.clear();
    g_mockHttp.wasEndCalled = false;
    g_mockHttp.postCallCount = 0;
    g_mockHttp.timeoutMs = 5000;
    g_mockHttp.useLiveTransport = false;
}

/**
 * @brief Helper executing a real HTTP POST request over Windows sockets.
 */
inline int performLiveHttpPost(
    const std::string& url,
    const std::map<std::string, std::string>& headers,
    const std::string& payload,
    uint32_t timeoutMs,
    std::string& outBody
) {
    std::string host = "127.0.0.1";
    std::string portStr = "5000";
    std::string path = "/api/scans";

    if (url.rfind("http://", 0) == 0) {
        std::string rest = url.substr(7);
        size_t slashPos = rest.find('/');
        std::string hostPort;
        if (slashPos != std::string::npos) {
            hostPort = rest.substr(0, slashPos);
            path = rest.substr(slashPos);
        } else {
            hostPort = rest;
            path = "/";
        }
        size_t colonPos = hostPort.find(':');
        if (colonPos != std::string::npos) {
            host = hostPort.substr(0, colonPos);
            portStr = hostPort.substr(colonPos + 1);
        } else {
            host = hostPort;
            portStr = "80";
        }
    }

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        return HTTPC_ERROR_CONNECTION_REFUSED;
    }

    struct addrinfo hints, *res = nullptr;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;

    if (getaddrinfo(host.c_str(), portStr.c_str(), &hints, &res) != 0 || !res) {
        WSACleanup();
        return HTTPC_ERROR_CONNECTION_REFUSED;
    }

    SOCKET sock = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (sock == INVALID_SOCKET) {
        freeaddrinfo(res);
        WSACleanup();
        return HTTPC_ERROR_CONNECTION_REFUSED;
    }

    DWORD tv = (timeoutMs > 0) ? timeoutMs : 5000;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (const char*)&tv, sizeof(tv));

    if (connect(sock, res->ai_addr, (int)res->ai_addrlen) == SOCKET_ERROR) {
        closesocket(sock);
        freeaddrinfo(res);
        WSACleanup();
        return HTTPC_ERROR_CONNECTION_REFUSED;
    }
    freeaddrinfo(res);

    std::string req = "POST " + path + " HTTP/1.1\r\n";
    req += "Host: " + host + ":" + portStr + "\r\n";
    req += "Connection: close\r\n";
    req += "Content-Length: " + std::to_string(payload.length()) + "\r\n";
    for (const auto& kv : headers) {
        if (kv.first == "Content-Length") continue;
        req += kv.first + ": " + kv.second + "\r\n";
    }
    req += "\r\n";
    req += payload;

    size_t totalSent = 0;
    while (totalSent < req.length()) {
        int sent = send(sock, req.c_str() + totalSent, (int)(req.length() - totalSent), 0);
        if (sent <= 0) {
            closesocket(sock);
            WSACleanup();
            return HTTPC_ERROR_SEND_PAYLOAD_FAILED;
        }
        totalSent += sent;
    }

    std::string responseData;
    char buf[4096];
    int bytesRead = 0;
    while ((bytesRead = recv(sock, buf, sizeof(buf) - 1, 0)) > 0) {
        buf[bytesRead] = '\0';
        responseData.append(buf, bytesRead);
    }
    closesocket(sock);
    WSACleanup();

    if (responseData.empty()) {
        return HTTPC_ERROR_READ_TIMEOUT;
    }

    int statusCode = 0;
    size_t firstLineEnd = responseData.find("\r\n");
    if (firstLineEnd != std::string::npos) {
        std::string firstLine = responseData.substr(0, firstLineEnd);
        size_t firstSpace = firstLine.find(' ');
        if (firstSpace != std::string::npos) {
            size_t secondSpace = firstLine.find(' ', firstSpace + 1);
            std::string codeStr = (secondSpace != std::string::npos)
                ? firstLine.substr(firstSpace + 1, secondSpace - firstSpace - 1)
                : firstLine.substr(firstSpace + 1);
            try {
                statusCode = std::stoi(codeStr);
            } catch (...) {
                statusCode = 0;
            }
        }
    }

    size_t headerEnd = responseData.find("\r\n\r\n");
    if (headerEnd != std::string::npos) {
        outBody = responseData.substr(headerEnd + 4);
    } else {
        outBody = "";
    }

    return statusCode;
}

class HTTPClient {
public:
    HTTPClient() {}
    ~HTTPClient() {}

    bool begin(WiFiClient&, const String& url) {
        g_mockHttp.lastUrl = url.c_str();
        g_mockHttp.wasEndCalled = false;
        return g_mockHttp.beginSuccess;
    }

    bool begin(const String& url) {
        g_mockHttp.lastUrl = url.c_str();
        g_mockHttp.wasEndCalled = false;
        return g_mockHttp.beginSuccess;
    }

    void addHeader(const String& name, const String& value) {
        g_mockHttp.lastHeaders[name.c_str()] = value.c_str();
    }

    void setTimeout(uint16_t timeout) {
        g_mockHttp.timeoutMs = timeout;
    }

    int POST(const String& payload) {
        g_mockHttp.lastRequestBody = payload.c_str();
        g_mockHttp.postCallCount++;

        if (g_mockHttp.useLiveTransport) {
            std::string liveBody;
            int code = performLiveHttpPost(
                g_mockHttp.lastUrl,
                g_mockHttp.lastHeaders,
                g_mockHttp.lastRequestBody,
                g_mockHttp.timeoutMs,
                liveBody
            );
            g_mockHttp.responseCode = code;
            g_mockHttp.responseBody = liveBody;
            return code;
        }

        return g_mockHttp.responseCode;
    }

    String getString() {
        return String(g_mockHttp.responseBody.c_str());
    }

    String errorToString(int error) {
        switch (error) {
            case HTTPC_ERROR_CONNECTION_REFUSED: return "connection refused";
            case HTTPC_ERROR_SEND_HEADER_FAILED: return "send header failed";
            case HTTPC_ERROR_SEND_PAYLOAD_FAILED: return "send payload failed";
            case HTTPC_ERROR_NOT_CONNECTED: return "not connected";
            case HTTPC_ERROR_CONNECTION_LOST: return "connection lost";
            case HTTPC_ERROR_NO_HTTP_SERVER: return "no HTTP server";
            case HTTPC_ERROR_TOO_LESS_RAM: return "too less ram";
            case HTTPC_ERROR_ENCODING: return "transfer-encoding not supported";
            case HTTPC_ERROR_STREAM_WRITE: return "stream write error";
            case HTTPC_ERROR_READ_TIMEOUT: return "read Timeout";
            default: return "unknown error";
        }
    }

    void end() {
        g_mockHttp.wasEndCalled = true;
    }
};

#endif // HTTP_CLIENT_H

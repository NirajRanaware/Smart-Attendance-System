/**
 * Smart Attendance Management System - Core Client Framework (app.js)
 * Global API Client, Asia/Kolkata Time Utilities, Health/Session Polling,
 * Live Clock, Mobile Navigation, and Toast Notifications.
 */

(function () {
    "use strict";

    // ==========================================================================
    // 1. ASIA/KOLKATA TIME & DATE UTILITIES
    // ==========================================================================
    const AppUtils = {
        /**
         * Returns today's date formatted as YYYY-MM-DD in the project's authoritative
         * Asia/Kolkata (+05:30) timezone.
         * @returns {string} e.g. "2026-11-07"
         */
        getKolkataTodayDateString() {
            try {
                const formatter = new Intl.DateTimeFormat("en-CA", {
                    timeZone: "Asia/Kolkata",
                    year: "numeric",
                    month: "2-digit",
                    day: "2-digit",
                });
                return formatter.format(new Date());
            } catch {
                // Fallback using UTC offset calculation for Asia/Kolkata (+330 minutes)
                const now = new Date();
                const kolkataOffsetMs = (5 * 60 + 30) * 60 * 1000;
                const kolkataTime = new Date(now.getTime() + (now.getTimezoneOffset() * 60000) + kolkataOffsetMs);
                const y = kolkataTime.getFullYear();
                const m = String(kolkataTime.getMonth() + 1).padStart(2, "0");
                const d = String(kolkataTime.getDate()).padStart(2, "0");
                return `${y}-${m}-${d}`;
            }
        },

        /**
         * Formats an ISO 8601 UTC timestamp (e.g. "2026-11-07T03:30:00Z") into a readable
         * time string in Asia/Kolkata timezone.
         * @param {string} isoString
         * @returns {string} e.g. "09:00:00 AM" or "09:00 AM"
         */
        formatScanTime(isoString) {
            if (!isoString) return "--:--";
            try {
                const date = new Date(isoString);
                if (isNaN(date.getTime())) return isoString;
                return new Intl.DateTimeFormat("en-IN", {
                    timeZone: "Asia/Kolkata",
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                    hour12: true,
                }).format(date);
            } catch {
                return isoString;
            }
        },

        /**
         * Formats current date for the dashboard header badge.
         * @returns {string} e.g. "Sat, Nov 7, 2026 (IST)"
         */
        formatKolkataDateDisplay() {
            try {
                const formatter = new Intl.DateTimeFormat("en-US", {
                    timeZone: "Asia/Kolkata",
                    weekday: "short",
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                });
                return `${formatter.format(new Date())} (IST)`;
            } catch {
                return `${this.getKolkataTodayDateString()} (IST)`;
            }
        },

        /**
         * Formats an ISO 8601 timestamp into a readable date and time in Asia/Kolkata timezone.
         * @param {string} isoString
         * @returns {string} e.g. "Nov 7, 2026, 03:30 PM"
         */
        formatDateTime(isoString) {
            if (!isoString) return "--";
            try {
                let cleanStr = isoString;
                if (!cleanStr.endsWith("Z") && !cleanStr.includes("+")) {
                    cleanStr += "Z";
                }
                const date = new Date(cleanStr);
                if (isNaN(date.getTime())) return isoString;
                return new Intl.DateTimeFormat("en-IN", {
                    timeZone: "Asia/Kolkata",
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                    hour12: true,
                }).format(date);
            } catch {
                return isoString;
            }
        },
    };

    // Expose AppUtils globally
    window.AppUtils = AppUtils;

    // ==========================================================================
    // 2. GLOBAL API CLIENT
    // ==========================================================================
    const ApiClient = {
        /**
         * Generic fetch wrapper with standard headers and robust JSON/Network error handling.
         * @param {string} endpoint - API route (e.g. '/api/health')
         * @param {object} options - Fetch options (method, body, headers, etc.)
         * @returns {Promise<{success: boolean, status: number, data: any, message: string}>}
         */
        async request(endpoint, options = {}) {
            const method = (options.method || "GET").toUpperCase();
            const headers = {
                "Content-Type": "application/json",
                ...(options.headers || {}),
            };

            // Automatically attach CSRF token to state-changing requests if present in DOM
            if (["POST", "PATCH", "PUT", "DELETE"].includes(method) && !headers["X-CSRF-Token"]) {
                const metaCsrf = document.querySelector('meta[name="csrf-token"]');
                if (metaCsrf && metaCsrf.getAttribute("content")) {
                    headers["X-CSRF-Token"] = metaCsrf.getAttribute("content");
                }
            }

            const config = {
                ...options,
                headers: headers,
            };

            try {
                const response = await fetch(endpoint, config);
                const status = response.status;
                let data = null;

                try {
                    data = await response.json();
                } catch {
                    data = { message: "Non-JSON response received from server." };
                }

                // If unauthorized, redirect to login page in browser session
                if (status === 401 && typeof window !== "undefined" && window.location) {
                    if (!window.location.pathname.startsWith("/login")) {
                        const safeNext = encodeURIComponent(window.location.pathname + window.location.search);
                        window.location.href = `/login?next=${safeNext}`;
                    }
                }

                return {
                    success: response.ok,
                    status: status,
                    data: data,
                    message: (data && data.message) || (response.ok ? "Success" : `HTTP Error ${status}`),
                };
            } catch (err) {
                return {
                    success: false,
                    status: 0,
                    data: null,
                    message: `Network/Connection Error: ${err.message}`,
                };
            }
        },

        get(endpoint) {
            return this.request(endpoint, { method: "GET" });
        },

        post(endpoint, payload) {
            return this.request(endpoint, {
                method: "POST",
                body: JSON.stringify(payload),
            });
        },

        patch(endpoint, payload = null) {
            return this.request(endpoint, {
                method: "PATCH",
                body: payload ? JSON.stringify(payload) : undefined,
            });
        },
    };

    // Expose ApiClient globally
    window.ApiClient = ApiClient;

    // ==========================================================================
    // 3. TOAST NOTIFICATION SYSTEM
    // ==========================================================================
    window.showToast = function (message, type = "info", duration = 4000) {
        const container = document.getElementById("toast-container");
        if (!container) return;

        const toast = document.createElement("div");
        toast.className = `toast toast-${type}`;
        
        let icon = "&#x2139;&#xFE0F;";
        if (type === "success") icon = "&#x2705;";
        if (type === "error") icon = "&#x274C;";

        toast.innerHTML = `
            <span class="toast-icon">${icon}</span>
            <span class="toast-message">${message}</span>
        `;

        container.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = "0";
            toast.style.transform = "translateY(10px)";
            toast.style.transition = "all 0.3s ease";
            setTimeout(() => toast.remove(), 300);
        }, duration);
    };

    // ==========================================================================
    // 4. LIVE CLOCK INITIALIZATION
    // ==========================================================================
    function initLiveClock() {
        const clockEl = document.getElementById("live-clock");
        const dateBadgeEl = document.getElementById("today-date-badge");

        function updateTime() {
            try {
                if (clockEl) {
                    const now = new Date();
                    clockEl.textContent = new Intl.DateTimeFormat("en-GB", {
                        timeZone: "Asia/Kolkata",
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                        hour12: false,
                    }).format(now);
                }
            } catch {
                if (clockEl) {
                    clockEl.textContent = new Date().toLocaleTimeString("en-GB", { hour12: false });
                }
            }

            if (dateBadgeEl && !dateBadgeEl.dataset.initialized) {
                dateBadgeEl.textContent = AppUtils.formatKolkataDateDisplay();
                dateBadgeEl.dataset.initialized = "true";
            }
        }

        updateTime();
        setInterval(updateTime, 1000);
    }

    // ==========================================================================
    // 5. SERVER HEALTH MONITOR (POLLING EVERY 30 SECONDS)
    // ==========================================================================
    let isHealthCheckInFlight = false;

    async function checkServerHealth() {
        if (isHealthCheckInFlight) return;
        isHealthCheckInFlight = true;

        const healthBadge = document.getElementById("topbar-health-badge");
        const healthText = document.getElementById("topbar-health-text");
        const sidebarDot = document.getElementById("sidebar-db-dot");
        const sidebarStatus = document.getElementById("sidebar-db-status");
        const kpiDbBadge = document.getElementById("kpi-db-badge");
        const kpiApiBadge = document.getElementById("kpi-api-badge");

        try {
            const result = await ApiClient.get("/api/health");

            if (result.success && result.data && result.data.database === "connected") {
                if (healthBadge) {
                    healthBadge.className = "health-badge";
                    healthText.textContent = "API: Connected";
                }
                if (sidebarDot) {
                    sidebarDot.className = "status-dot status-dot-active";
                }
                if (sidebarStatus) {
                    sidebarStatus.textContent = "SQLite: Connected";
                }
                if (kpiDbBadge) {
                    kpiDbBadge.className = "badge badge-success";
                    kpiDbBadge.textContent = "SQLite Active";
                }
                if (kpiApiBadge) {
                    kpiApiBadge.className = "badge badge-success";
                    kpiApiBadge.textContent = "Connected";
                }
            } else {
                if (healthBadge) {
                    healthBadge.className = "health-badge disconnected";
                    healthText.textContent = "API: Disconnected";
                }
                if (sidebarDot) {
                    sidebarDot.className = "status-dot";
                }
                if (sidebarStatus) {
                    sidebarStatus.textContent = "SQLite: Offline";
                }
                if (kpiDbBadge) {
                    kpiDbBadge.className = "badge badge-danger";
                    kpiDbBadge.textContent = "SQLite Offline";
                }
                if (kpiApiBadge) {
                    kpiApiBadge.className = "badge badge-danger";
                    kpiApiBadge.textContent = "Disconnected";
                }
            }
        } catch {
            if (healthBadge) {
                healthBadge.className = "health-badge disconnected";
                healthText.textContent = "API: Offline";
            }
            if (kpiDbBadge) {
                kpiDbBadge.className = "badge badge-danger";
                kpiDbBadge.textContent = "Offline";
            }
            if (kpiApiBadge) {
                kpiApiBadge.className = "badge badge-danger";
                kpiApiBadge.textContent = "Offline";
            }
        } finally {
            isHealthCheckInFlight = false;
        }
    }

    // Expose for explicit calls
    window.checkServerHealth = checkServerHealth;

    // ==========================================================================
    // 6. ACTIVE SESSION POLLER (TOPBAR INDICATOR - EVERY 30 SECONDS)
    // ==========================================================================
    let isActiveSessionInFlight = false;

    async function checkActiveSession() {
        if (isActiveSessionInFlight) return;
        isActiveSessionInFlight = true;

        const pill = document.getElementById("topbar-session-pill");
        const pillText = document.getElementById("topbar-session-text");
        if (!pill || !pillText) {
            isActiveSessionInFlight = false;
            return;
        }

        try {
            const result = await ApiClient.get("/api/sessions/active");

            if (result.success && result.data && result.data.session) {
                const sess = result.data.session;
                pill.className = "active-session-pill active";
                pillText.textContent = `Active: ${sess.course_name} (${sess.start_time})`;
                pill.title = `Current Active Lecture: ${sess.course_name} on ${sess.session_date}`;
            } else if (result.success && result.data && result.data.session === null) {
                pill.className = "active-session-pill";
                pillText.textContent = "No Active Session";
                pill.title = "No class session is currently active for attendance.";
            } else {
                pill.className = "active-session-pill";
                pillText.textContent = "No Active Session";
                pill.title = "No active session detected.";
            }
        } catch {
            pill.className = "active-session-pill";
            pillText.textContent = "Session: Offline";
        } finally {
            isActiveSessionInFlight = false;
        }
    }

    // Expose for explicit calls
    window.checkActiveSession = checkActiveSession;

    // ==========================================================================
    // 7. ACCESSIBLE MOBILE NAVIGATION DRAWER
    // ==========================================================================
    function initMobileMenu() {
        const toggleBtn = document.getElementById("mobile-menu-btn");
        const sidebar = document.getElementById("sidebar");
        const backdrop = document.getElementById("sidebar-backdrop");

        if (!toggleBtn || !sidebar) return;

        function openSidebar() {
            sidebar.classList.add("open");
            if (backdrop) {
                backdrop.classList.add("active");
                backdrop.setAttribute("aria-hidden", "false");
            }
            toggleBtn.setAttribute("aria-expanded", "true");
            document.body.classList.add("sidebar-open");
        }

        function closeSidebar(restoreFocus = false) {
            sidebar.classList.remove("open");
            if (backdrop) {
                backdrop.classList.remove("active");
                backdrop.setAttribute("aria-hidden", "true");
            }
            toggleBtn.setAttribute("aria-expanded", "false");
            document.body.classList.remove("sidebar-open");
            if (restoreFocus && toggleBtn) {
                toggleBtn.focus();
            }
        }

        toggleBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (sidebar.classList.contains("open")) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });

        if (backdrop) {
            backdrop.addEventListener("click", () => {
                closeSidebar();
            });
        }

        // Close when pressing Escape key
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && sidebar.classList.contains("open")) {
                closeSidebar(true);
            }
        });

        // Close on navigation link click when on mobile
        const navLinks = sidebar.querySelectorAll(".nav-link");
        navLinks.forEach((link) => {
            link.addEventListener("click", () => {
                if (window.innerWidth <= 992 || sidebar.classList.contains("open")) {
                    closeSidebar();
                }
            });
        });

        // Close on outside click
        document.addEventListener("click", (e) => {
            if (
                !sidebar.contains(e.target) &&
                !toggleBtn.contains(e.target) &&
                sidebar.classList.contains("open")
            ) {
                closeSidebar();
            }
        });

        // Expose helpers globally
        window.openMobileSidebar = openSidebar;
        window.closeMobileSidebar = closeSidebar;
    }

    // ==========================================================================
    // 8. LIFECYCLE & POLLING COORDINATOR
    // ==========================================================================
    let healthIntervalId = null;
    let sessionIntervalId = null;

    function startGlobalPolling() {
        // Run immediately on page load
        checkServerHealth();
        checkActiveSession();

        // Clear existing intervals to avoid duplicates
        if (healthIntervalId) clearInterval(healthIntervalId);
        if (sessionIntervalId) clearInterval(sessionIntervalId);

        // Health check every 30 seconds
        healthIntervalId = setInterval(() => {
            if (!document.hidden) {
                checkServerHealth();
            }
        }, 30000);

        // Active session check every 30 seconds
        sessionIntervalId = setInterval(() => {
            if (!document.hidden) {
                checkActiveSession();
            }
        }, 30000);
    }

    // Pause polling when tab is hidden, resume immediately when tab becomes visible
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            checkServerHealth();
            checkActiveSession();
        }
    });

    // ==========================================================================
    // DOM READY INITIALIZATION
    // ==========================================================================
    document.addEventListener("DOMContentLoaded", () => {
        initLiveClock();
        initMobileMenu();
        startGlobalPolling();
    });
})();

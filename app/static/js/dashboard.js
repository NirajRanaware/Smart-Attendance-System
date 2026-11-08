/**
 * Smart Attendance Management System - Dashboard Client Controller (dashboard.js)
 * Live KPI data integration, real-time class session monitoring, daily report statistics,
 * and recent RFID scan activity feed with automatic polling.
 */

(function () {
    "use strict";

    // Helper: Escape HTML strings to prevent XSS injection in dynamic lists
    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // Helper: Extract student initials (e.g. "Alice Johnson" -> "AJ")
    function getInitials(name) {
        if (!name) return "S";
        const parts = name.trim().split(/\s+/);
        if (parts.length === 1) return parts[0].substring(0, 2).toUpperCase();
        return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }

    // Guard flags to prevent overlapping in-flight API requests
    let isStudentsInFlight = false;
    let isSessionInFlight = false;
    let isTodayStatsInFlight = false;
    let isAttendanceInFlight = false;

    // ==========================================================================
    // 1. KPI 1: TOTAL REGISTERED STUDENTS
    // API: GET /api/students
    // ==========================================================================
    async function loadStudentCount() {
        const countEl = document.getElementById("kpi-students-count");
        const subtextEl = document.getElementById("kpi-students-subtext");
        if (!countEl) return;

        if (isStudentsInFlight) return;
        isStudentsInFlight = true;

        countEl.classList.add("skeleton");

        try {
            const res = await window.ApiClient.get("/api/students");
            if (res.success && res.data) {
                const students = Array.isArray(res.data.students) ? res.data.students : [];
                const totalCount = typeof res.data.count === "number" ? res.data.count : students.length;
                // Defensive normalization: handles both JSON booleans (true/false) and SQLite integers (1/0)
                const isActiveStudent = (s) => Boolean(s) && (s.active === true || Number(s.active) === 1);
                const activeCount = students.filter(isActiveStudent).length;
                const inactiveCount = totalCount - activeCount;

                countEl.textContent = activeCount;
                if (subtextEl) {
                    if (inactiveCount > 0) {
                        subtextEl.textContent = `${activeCount} active (${inactiveCount} inactive)`;
                    } else {
                        subtextEl.textContent = `${activeCount} active student records`;
                    }
                }
            } else {
                countEl.textContent = "--";
                if (subtextEl) {
                    subtextEl.textContent = "Failed to load student roster";
                }
            }
        } catch {
            countEl.textContent = "--";
            if (subtextEl) {
                subtextEl.textContent = "Failed to load student roster";
            }
        } finally {
            countEl.classList.remove("skeleton");
            isStudentsInFlight = false;
        }
    }

    // ==========================================================================
    // 2. KPI 2: ACTIVE CLASS SESSION
    // API: GET /api/sessions/active
    // ==========================================================================
    async function loadActiveSessionCard() {
        const courseEl = document.getElementById("kpi-session-course");
        const timeEl = document.getElementById("kpi-session-time");
        const streamBadge = document.getElementById("activity-stream-badge");
        if (!courseEl) return;

        if (isSessionInFlight) return;
        isSessionInFlight = true;

        courseEl.classList.add("skeleton");

        try {
            const res = await window.ApiClient.get("/api/sessions/active");
            if (res.success && res.data && res.data.session) {
                const sess = res.data.session;
                courseEl.textContent = sess.course_name;
                courseEl.title = `${sess.course_name} (${sess.session_date})`;
                if (timeEl) {
                    const timeRange = sess.end_time ? `${sess.start_time} - ${sess.end_time}` : sess.start_time;
                    timeEl.textContent = `Scheduled: ${timeRange} (${sess.session_date})`;
                }
                if (streamBadge) {
                    streamBadge.className = "badge badge-success";
                    streamBadge.textContent = "Live Stream Active";
                }
            } else if (res.success && res.data && res.data.session === null) {
                courseEl.textContent = "No Active Session";
                courseEl.title = "No class session is currently active";
                if (timeEl) {
                    timeEl.textContent = "No lecture currently active";
                }
                if (streamBadge) {
                    streamBadge.className = "badge badge-neutral";
                    streamBadge.textContent = "Standby";
                }
            } else {
                courseEl.textContent = "Unavailable";
                if (timeEl) {
                    timeEl.textContent = "Could not reach session API";
                }
                if (streamBadge) {
                    streamBadge.className = "badge badge-danger";
                    streamBadge.textContent = "Error";
                }
            }
        } catch {
            courseEl.textContent = "Unavailable";
            if (timeEl) {
                timeEl.textContent = "Could not reach session API";
            }
            if (streamBadge) {
                streamBadge.className = "badge badge-danger";
                streamBadge.textContent = "Error";
            }
        } finally {
            courseEl.classList.remove("skeleton");
            isSessionInFlight = false;
        }
    }

    // ==========================================================================
    // 3. KPI 3: TODAY'S ATTENDANCE RATE
    // API: GET /api/reports/daily?date=YYYY-MM-DD (Asia/Kolkata)
    // ==========================================================================
    async function loadTodayStats() {
        const rateEl = document.getElementById("kpi-attendance-rate");
        const countEl = document.getElementById("kpi-attendance-counts");
        if (!rateEl) return;

        if (isTodayStatsInFlight) return;
        isTodayStatsInFlight = true;

        rateEl.classList.add("skeleton");

        try {
            const today = window.AppUtils ? window.AppUtils.getKolkataTodayDateString() : new Date().toISOString().split("T")[0];
            const res = await window.ApiClient.get(`/api/reports/daily?date=${today}`);

            if (res.success && res.data && res.data.summary) {
                const summary = res.data.summary;
                const pct = summary.overall_attendance_percentage;
                rateEl.textContent = `${pct}%`;

                if (countEl) {
                    if (summary.total_sessions === 0) {
                        countEl.textContent = "No class sessions scheduled today";
                    } else {
                        const sessText = summary.total_sessions === 1 ? "1 session" : `${summary.total_sessions} sessions`;
                        countEl.textContent = `${summary.total_present} present / ${summary.total_enrollments} enrolled (${sessText})`;
                    }
                }
            } else {
                rateEl.textContent = "--%";
                if (countEl) {
                    countEl.textContent = "Failed to load daily report";
                }
            }
        } catch {
            rateEl.textContent = "--%";
            if (countEl) {
                countEl.textContent = "Failed to load daily report";
            }
        } finally {
            rateEl.classList.remove("skeleton");
            isTodayStatsInFlight = false;
        }
    }

    // ==========================================================================
    // 4. RECENT ATTENDANCE ACTIVITY STREAM
    // API: GET /api/attendance?date=YYYY-MM-DD (Asia/Kolkata)
    // ==========================================================================
    async function loadRecentAttendance() {
        const container = document.getElementById("activity-container");
        if (!container) return;

        if (isAttendanceInFlight) return;
        isAttendanceInFlight = true;

        try {
            const today = window.AppUtils ? window.AppUtils.getKolkataTodayDateString() : new Date().toISOString().split("T")[0];
            const res = await window.ApiClient.get(`/api/attendance?date=${today}`);

            if (res.success && res.data) {
                const records = Array.isArray(res.data.attendance) ? res.data.attendance : [];

                if (records.length === 0) {
                    // Empty state when no scans have been recorded today
                    container.innerHTML = `
                        <div class="empty-state" id="dashboard-empty-state">
                            <div class="empty-icon">&#x1F4E1;</div>
                            <h3 class="empty-title">Waiting for RFID Scans</h3>
                            <p class="empty-description">When students tap their RFID cards at the entrance or via the Python simulator (<code class="code-inline">simulate_scan.py</code>), verified scan events will stream here live.</p>
                            <a href="/attendance" class="btn btn-secondary btn-sm" style="margin-top: 12px;">Open Live Attendance View &rarr;</a>
                        </div>
                    `;
                } else {
                    // Sort descending by scanned_at so newest scan appears at the top
                    const sortedRecords = [...records].sort((a, b) => {
                        const timeA = new Date(a.scanned_at || 0).getTime();
                        const timeB = new Date(b.scanned_at || 0).getTime();
                        return timeB - timeA;
                    });

                    // Take the 10 most recent scans for the dashboard stream
                    const displayScans = sortedRecords.slice(0, 10);

                    const itemsHtml = displayScans.map(item => {
                        const formattedTime = window.AppUtils ? window.AppUtils.formatScanTime(item.scanned_at) : item.scanned_at;
                        const initials = getInitials(item.student_name);
                        const safeName = escapeHtml(item.student_name || "Unknown Student");
                        const safeRoll = escapeHtml(item.roll_number || "N/A");
                        const safeCourse = escapeHtml(item.course_name || "Lecture");
                        const safeStatus = escapeHtml(item.status || "Present");
                        const safeSource = escapeHtml(item.source || "hardware");

                        return `
                            <div class="activity-item">
                                <div class="activity-left">
                                    <div class="activity-avatar" title="${safeName}">${initials}</div>
                                    <div class="activity-details">
                                        <span class="activity-student-name">${safeName}</span>
                                        <span class="activity-meta">
                                            <span>Roll: ${safeRoll}</span> &bull; 
                                            <span>${safeCourse}</span>
                                        </span>
                                    </div>
                                </div>
                                <div class="activity-right">
                                    <span class="activity-time">${formattedTime}</span>
                                    <div class="activity-badges">
                                        <span class="badge badge-success">${safeStatus}</span>
                                        <span class="badge badge-neutral">${safeSource}</span>
                                    </div>
                                </div>
                            </div>
                        `;
                    }).join("");

                    container.innerHTML = `<div class="activity-list" id="activity-list">${itemsHtml}</div>`;
                }
            } else {
                container.innerHTML = `
                    <div class="activity-error">
                        <strong>&#x26A0;&#xFE0F; Attendance Feed Error:</strong>
                        <span>Could not retrieve today's attendance logs from the server.</span>
                    </div>
                `;
            }
        } catch {
            container.innerHTML = `
                <div class="activity-error">
                    <strong>&#x26A0;&#xFE0F; Attendance Feed Error:</strong>
                    <span>Could not retrieve today's attendance logs from the server.</span>
                </div>
            `;
        } finally {
            isAttendanceInFlight = false;
        }
    }

    // ==========================================================================
    // 5. MASTER DASHBOARD REFRESH ORCHESTRATION
    // ==========================================================================
    async function refreshDashboard(isManual = false) {
        const btn = document.getElementById("btn-refresh-dashboard");
        if (isManual && btn) {
            btn.disabled = true;
            btn.innerHTML = `<span class="btn-icon">&#x21BB;</span> Refreshing...`;
        }

        try {
            // Trigger all metric and activity retrievals concurrently
            await Promise.allSettled([
                loadStudentCount(),
                loadActiveSessionCard(),
                loadTodayStats(),
                loadRecentAttendance(),
                typeof window.checkServerHealth === "function" ? window.checkServerHealth() : Promise.resolve(),
                typeof window.checkActiveSession === "function" ? window.checkActiveSession() : Promise.resolve(),
            ]);

            if (isManual && window.showToast) {
                window.showToast("Dashboard metrics refreshed.", "success", 2500);
            }
        } catch {
            if (isManual && window.showToast) {
                window.showToast("Failed to refresh some metrics.", "error", 3000);
            }
        } finally {
            if (isManual && btn) {
                btn.disabled = false;
                btn.innerHTML = `<span class="btn-icon">&#x21BB;</span> Refresh Data`;
            }
        }
    }

    // Expose for debugging and manual triggers
    window.refreshDashboard = refreshDashboard;

    // ==========================================================================
    // 6. DASHBOARD TIMERS & EVENT LISTENERS
    // ==========================================================================
    let statsTimerId = null;
    let fastTimerId = null;

    function startDashboardPolling() {
        // Initial load
        refreshDashboard(false);

        // Clear existing dashboard timers
        if (statsTimerId) clearInterval(statsTimerId);
        if (fastTimerId) clearInterval(fastTimerId);

        // Attendance stream and active session refresh every 30 seconds
        fastTimerId = setInterval(() => {
            if (!document.hidden) {
                loadActiveSessionCard();
                loadRecentAttendance();
            }
        }, 30000);

        // Overall statistics (student roster count and daily percentages) refresh every 60 seconds
        statsTimerId = setInterval(() => {
            if (!document.hidden) {
                loadStudentCount();
                loadTodayStats();
            }
        }, 60000);
    }

    // Resume immediately when user focuses back on the tab
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            loadActiveSessionCard();
            loadRecentAttendance();
            loadStudentCount();
            loadTodayStats();
        }
    });

    document.addEventListener("DOMContentLoaded", () => {
        startDashboardPolling();

        const refreshBtn = document.getElementById("btn-refresh-dashboard");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", () => refreshDashboard(true));
        }
    });
})();

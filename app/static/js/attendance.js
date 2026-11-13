/**
 * Smart Attendance Management System - Live Attendance Controller (attendance.js)
 * Manages real-time attendance polling, active session detection, summary calculations,
 * live scan feedback status panel, and simulated RFID card scanning.
 */

(function () {
    "use strict";

    // ==========================================================================
    // 1. STATE & UTILITIES
    // ==========================================================================
    let activeSession = null;
    let allStudents = [];
    let currentSessionAttendance = [];
    let lastScannedUid = "";
    let isPollingActive = true;
    let pollingTimerId = null;
    let isPollInFlight = false;
    let isSubmittingScan = false;
    const POLLING_INTERVAL_MS = 4000; // 4 seconds

    // Defensive active student normalization
    const isActiveStudent = (s) => Boolean(s) && (s.active === true || Number(s.active) === 1);

    // Escape HTML to prevent XSS injection
    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    /**
     * Resolves the target query date: active session date or today's date in Asia/Kolkata.
     */
    function getTargetDate() {
        if (activeSession && activeSession.session_date) {
            return activeSession.session_date;
        }
        if (window.AppUtils && typeof window.AppUtils.getKolkataTodayDateString === "function") {
            return window.AppUtils.getKolkataTodayDateString();
        }
        return new Date().toISOString().split("T")[0];
    }

    // ==========================================================================
    // 2. LIVE SCAN STATUS PANEL FEEDBACK CONTROLLER
    // ==========================================================================
    function updateScanFeedback(type, title, subtitle, details = {}) {
        const box = document.getElementById("scan-feedback-box");
        const iconEl = document.getElementById("scan-feedback-icon");
        const titleEl = document.getElementById("scan-feedback-title");
        const subtitleEl = document.getElementById("scan-feedback-subtitle");
        const badgeEl = document.getElementById("scan-status-badge");
        const detailsContainer = document.getElementById("scan-feedback-details");

        const studentNameEl = document.getElementById("feedback-student-name");
        const rollNumberEl = document.getElementById("feedback-roll-number");
        const attendanceStatusEl = document.getElementById("feedback-attendance-status");
        const scanTimeEl = document.getElementById("feedback-scan-time");
        const courseNameEl = document.getElementById("feedback-course-name");
        const messageEl = document.getElementById("feedback-message");

        const rowRoll = document.getElementById("row-roll-number");
        const rowStudent = document.getElementById("row-student-name");
        const rowAttendance = document.getElementById("row-attendance-status");
        const rowScanTime = document.getElementById("row-scan-time");
        const rowCourse = document.getElementById("row-course-name");
        const rowMessage = document.getElementById("row-message");

        if (!box) return;

        // Reset classes
        box.className = `scan-feedback-box status-${type}`;

        // Map status icons & badge styles
        let icon = "&#x23F3;";
        let badgeText = "Standby";
        let badgeClass = "badge badge-neutral";

        if (type === "success") {
            icon = "&#x2705;";
            badgeText = "Success (201)";
            badgeClass = "badge badge-success";
        } else if (type === "duplicate") {
            icon = "&#x26A0;&#xFE0F;";
            badgeText = "Duplicate (409)";
            badgeClass = "badge badge-warning";
        } else if (type === "unregistered") {
            icon = "&#x2753;";
            badgeText = "Unknown Card (404)";
            badgeClass = "badge badge-danger";
        } else if (type === "inactive") {
            icon = "&#x1F6AB;";
            badgeText = "Inactive Account (403)";
            badgeClass = "badge badge-danger";
        } else if (type === "no-session") {
            icon = "&#x23F9;";
            badgeText = "No Session (409)";
            badgeClass = "badge badge-warning";
        } else if (type === "error") {
            icon = "&#x274C;";
            badgeText = "Error";
            badgeClass = "badge badge-danger";
        }

        if (iconEl) iconEl.innerHTML = icon;
        if (titleEl) titleEl.textContent = title;
        if (subtitleEl) subtitleEl.textContent = subtitle;
        if (badgeEl) {
            badgeEl.className = badgeClass;
            badgeEl.textContent = badgeText;
        }

        // Show/populate details if available
        if (details && (details.studentName || details.rollNumber || details.message || details.attendanceStatus)) {
            if (detailsContainer) detailsContainer.style.display = "flex";

            if (rollNumberEl) rollNumberEl.textContent = details.rollNumber || "--";
            if (rowRoll) rowRoll.style.display = details.rollNumber ? "flex" : "none";

            if (studentNameEl) studentNameEl.textContent = details.studentName || "--";
            if (rowStudent) rowStudent.style.display = details.studentName ? "flex" : "none";

            if (attendanceStatusEl) {
                attendanceStatusEl.textContent = details.attendanceStatus || "--";
                if (details.attendanceStatus === "PRESENT") {
                    attendanceStatusEl.className = "feedback-value text-success font-weight-bold";
                } else if (details.attendanceStatus === "ALREADY PRESENT") {
                    attendanceStatusEl.className = "feedback-value text-warning font-weight-bold";
                } else {
                    attendanceStatusEl.className = "feedback-value font-weight-bold";
                }
            }
            if (rowAttendance) rowAttendance.style.display = details.attendanceStatus ? "flex" : "none";

            if (scanTimeEl) {
                if (details.scanTime && window.AppUtils && typeof window.AppUtils.formatScanTime === "function") {
                    scanTimeEl.textContent = window.AppUtils.formatScanTime(details.scanTime);
                } else {
                    scanTimeEl.textContent = details.scanTime || "--";
                }
            }
            if (rowScanTime) rowScanTime.style.display = details.scanTime ? "flex" : "none";

            if (courseNameEl) courseNameEl.textContent = details.courseName || (activeSession ? activeSession.course_name : "--");
            if (rowCourse) rowCourse.style.display = details.courseName ? "flex" : "none";

            if (messageEl) messageEl.textContent = details.message || "--";
            if (rowMessage) rowMessage.style.display = details.message ? "flex" : "none";
        } else {
            if (detailsContainer) detailsContainer.style.display = "none";
        }
    }

    // ==========================================================================
    // 3. DATA FETCHING: ACTIVE SESSION & ROSTER
    // ==========================================================================
    async function fetchActiveSession() {
        const heroCard = document.getElementById("live-active-session-card");
        const heroDot = document.getElementById("active-session-dot");
        const heroStatus = document.getElementById("active-session-status");
        const heroCourse = document.getElementById("active-course-title");
        const heroDate = document.getElementById("active-session-date");
        const heroTime = document.getElementById("active-session-time");
        const heroId = document.getElementById("active-session-id");
        const heroBadge = document.getElementById("active-session-badge");

        try {
            const res = await window.ApiClient.get("/api/sessions/active");

            if (res.success && res.data && res.data.session) {
                activeSession = res.data.session;

                if (heroCard) heroCard.classList.add("is-active");
                if (heroDot) heroDot.className = "status-dot status-dot-active";
                if (heroStatus) heroStatus.textContent = "CURRENT ACTIVE SESSION";
                if (heroCourse) heroCourse.textContent = activeSession.course_name;
                if (heroDate) heroDate.textContent = activeSession.session_date;
                if (heroTime) {
                    heroTime.textContent = activeSession.end_time
                        ? `${activeSession.start_time} - ${activeSession.end_time}`
                        : activeSession.start_time;
                }
                if (heroId) heroId.textContent = activeSession.id;

                if (heroBadge) {
                    heroBadge.className = "badge badge-success";
                    heroBadge.textContent = "Active Now";
                }
            } else {
                activeSession = null;

                if (heroCard) heroCard.classList.remove("is-active");
                if (heroDot) heroDot.className = "status-dot";
                if (heroStatus) heroStatus.textContent = "NO ACTIVE SESSION";
                if (heroCourse) heroCourse.textContent = "No Active Class Session";
                if (heroDate) heroDate.textContent = "--";
                if (heroTime) heroTime.textContent = "--";
                if (heroId) heroId.textContent = "--";

                if (heroBadge) {
                    heroBadge.className = "badge badge-neutral";
                    heroBadge.textContent = "Inactive";
                }
            }
        } catch (err) {
            console.error("Failed to fetch active session:", err);
            if (heroCourse) heroCourse.textContent = "Unable to check active session";
        }
    }

    async function fetchStudents() {
        try {
            const res = await window.ApiClient.get("/api/students");
            if (res.success && res.data) {
                allStudents = Array.isArray(res.data.students) ? res.data.students : [];
                populateStudentSelectDropdown();
                updateSummaryCards();
            }
        } catch (err) {
            console.error("Failed to fetch registered students:", err);
        }
    }

    function populateStudentSelectDropdown() {
        const select = document.getElementById("sim-student-select");
        if (!select) return;

        // Preserve current selection if possible
        const previousVal = select.value;

        // Build options for registered students
        const optionsHtml = ['<option value="">-- Choose registered student --</option>'];

        allStudents.forEach((s) => {
            const active = isActiveStudent(s);
            const statusTag = active ? "" : " [INACTIVE]";
            const safeUid = escapeHtml(s.card_uid);
            const label = `${escapeHtml(s.roll_number)} - ${escapeHtml(s.full_name)}${statusTag} (${safeUid})`;
            optionsHtml.push(`<option value="${safeUid}" data-active="${active}">${label}</option>`);
        });

        select.innerHTML = optionsHtml.join("");
        if (previousVal) {
            select.value = previousVal;
        }
    }

    // ==========================================================================
    // 4. ATTENDANCE STREAM RETRIEVAL (GET /api/attendance?date=...)
    // ==========================================================================
    async function fetchAttendance() {
        const targetDate = getTargetDate();
        const table = document.getElementById("attendance-table");
        const tbody = document.getElementById("attendance-table-body");
        const emptyState = document.getElementById("attendance-empty-state");
        const noSessionState = document.getElementById("attendance-no-session-state");
        const errorState = document.getElementById("attendance-error-state");
        const badge = document.getElementById("attendance-count-badge");
        const toolbarInfo = document.getElementById("attendance-toolbar-info");

        try {
            const res = await window.ApiClient.get(`/api/attendance?date=${encodeURIComponent(targetDate)}`);

            if (res.success && res.data) {
                if (errorState) errorState.style.display = "none";

                const allDayAttendance = Array.isArray(res.data.attendance) ? res.data.attendance : [];

                // Filter attendance specifically for the active session (never mix sessions)
                if (activeSession && activeSession.id) {
                    currentSessionAttendance = allDayAttendance.filter((rec) => rec.session_id === activeSession.id);
                } else {
                    currentSessionAttendance = [];
                }

                // Sort newest scan first (scanned_at descending)
                currentSessionAttendance.sort((a, b) => {
                    const timeA = new Date(a.scanned_at || 0).getTime();
                    const timeB = new Date(b.scanned_at || 0).getTime();
                    return timeB - timeA;
                });

                // Update summary metrics
                updateSummaryCards();

                // Render Table or Appropriate State
                if (!activeSession) {
                    if (table) table.style.display = "none";
                    if (emptyState) emptyState.style.display = "none";
                    if (noSessionState) noSessionState.style.display = "flex";
                    if (badge) badge.textContent = "No Active Session";
                    if (toolbarInfo) toolbarInfo.textContent = "Attendance scanning is inactive because no lecture session is active.";
                    return;
                }

                if (noSessionState) noSessionState.style.display = "none";

                if (currentSessionAttendance.length === 0) {
                    if (table) table.style.display = "none";
                    if (emptyState) emptyState.style.display = "flex";
                    if (badge) badge.textContent = "0 Scans";
                    if (toolbarInfo) toolbarInfo.textContent = `Active lecture: "${activeSession.course_name}". Waiting for student scans...`;
                    return;
                }

                if (emptyState) emptyState.style.display = "none";
                if (table) table.style.display = "table";
                if (badge) badge.textContent = `${currentSessionAttendance.length} Present`;
                if (toolbarInfo) toolbarInfo.textContent = `Showing attendance for "${activeSession.course_name}" (Session #${activeSession.id}).`;

                // Render table rows
                if (tbody) {
                    tbody.innerHTML = currentSessionAttendance
                        .map((rec, index) => {
                            const rowNumber = currentSessionAttendance.length - index;
                            const safeRoll = escapeHtml(rec.roll_number);
                            const safeName = escapeHtml(rec.student_name);
                            const safeCourse = escapeHtml(rec.course_name);
                            const safeDate = escapeHtml(rec.session_date);
                            const safeSource = escapeHtml(rec.source || "RFID_READER");

                            const formattedTime = (window.AppUtils && typeof window.AppUtils.formatScanTime === "function")
                                ? window.AppUtils.formatScanTime(rec.scanned_at)
                                : escapeHtml(rec.scanned_at);

                            return `
                                <tr>
                                    <td class="cell-mono">${rowNumber}</td>
                                    <td class="cell-mono cell-bold">${safeRoll}</td>
                                    <td class="cell-bold">${safeName}</td>
                                    <td>${safeCourse}</td>
                                    <td class="cell-mono">${safeDate}</td>
                                    <td class="cell-mono">${formattedTime}</td>
                                    <td><span class="badge badge-success">Present</span></td>
                                    <td><span class="cell-mono">${safeSource}</span></td>
                                </tr>
                            `;
                        })
                        .join("");
                }
            } else {
                if (badge) badge.textContent = "Error";
                if (table) table.style.display = "none";
                if (emptyState) emptyState.style.display = "none";
                if (noSessionState) noSessionState.style.display = "none";
                if (errorState) errorState.style.display = "flex";
            }
        } catch (err) {
            console.error("Failed to fetch live attendance stream:", err);
            if (badge) badge.textContent = "Error";
            if (table) table.style.display = "none";
            if (emptyState) emptyState.style.display = "none";
            if (noSessionState) noSessionState.style.display = "none";
            if (errorState) errorState.style.display = "flex";
        }
    }

    // ==========================================================================
    // 5. SUMMARY KPI CALCULATION
    // ==========================================================================
    function updateSummaryCards() {
        const statTotal = document.getElementById("stat-total-students");
        const statActive = document.getElementById("stat-active-students");
        const statPresent = document.getElementById("stat-present-session");
        const statRemaining = document.getElementById("stat-remaining-session");

        const totalRegistered = allStudents.length;
        const eligibleActive = allStudents.filter(isActiveStudent).length;

        if (statTotal) statTotal.textContent = totalRegistered;
        if (statActive) statActive.textContent = eligibleActive;

        if (activeSession) {
            const presentCount = currentSessionAttendance.length;
            // Formula: Math.max(0, eligibleActive - presentCount) (Never negative)
            const remainingCount = Math.max(0, eligibleActive - presentCount);

            if (statPresent) statPresent.textContent = presentCount;
            if (statRemaining) statRemaining.textContent = remainingCount;
        } else {
            if (statPresent) statPresent.textContent = "--";
            if (statRemaining) statRemaining.textContent = "--";
        }
    }

    // ==========================================================================
    // 6. RFID SCANNER SIMULATOR (POST /api/scans)
    // ==========================================================================
    async function handleSimulateScan(e) {
        if (e) e.preventDefault();
        if (isSubmittingScan) return;

        const uidInput = document.getElementById("sim-card-uid");
        const sourceInput = document.getElementById("sim-source");
        const submitBtn = document.getElementById("btn-submit-scan");

        const rawUid = (uidInput ? uidInput.value : "").trim();
        const source = (sourceInput && sourceInput.value.trim()) ? sourceInput.value.trim() : "SIMULATOR";

        if (!rawUid) {
            updateScanFeedback("error", "Empty RFID Card UID", "Please enter or select an RFID card UID to simulate a scan.");
            if (uidInput) uidInput.focus();
            return;
        }

        const normalizedUid = rawUid.toUpperCase();
        if (normalizedUid.length < 4 || normalizedUid.length > 50) {
            updateScanFeedback("error", "Invalid Card UID Length", "RFID UID must be between 4 and 50 characters.");
            if (uidInput) uidInput.focus();
            return;
        }

        const uidRegex = /^[A-Z0-9_-]+$/;
        if (!uidRegex.test(normalizedUid)) {
            updateScanFeedback("error", "Invalid UID Characters", "RFID UID must contain only alphanumeric characters, dashes, or underscores.");
            if (uidInput) uidInput.focus();
            return;
        }

        // Remember last UID for duplicate test chip
        lastScannedUid = normalizedUid;

        // In-flight guard & button disabled state
        isSubmittingScan = true;
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<span class="btn-icon">&#x231B;</span> Scanning...`;
        }

        try {
            const payload = {
                card_uid: normalizedUid,
                source: source,
            };

            const res = await window.ApiClient.post("/api/scans", payload);

            if (res.success && res.status === 201) {
                // HTTP 201: Successful Attendance Recorded - Student Identified
                const att = (res.data && res.data.attendance) || {};
                const stu = (res.data && res.data.student) || {};
                const rollNo = stu.roll_number || att.roll_number;
                const stuName = stu.student_name || att.student_name;

                updateScanFeedback("success", "STUDENT IDENTIFIED", res.data.message || "Attendance marked Present.", {
                    rollNumber: rollNo,
                    studentName: stuName,
                    attendanceStatus: "PRESENT",
                    scanTime: att.scanned_at,
                    courseName: att.course_name,
                    message: "Marked Present in active lecture session.",
                });

                if (window.showToast) {
                    window.showToast(`Attendance marked for ${stuName} (${rollNo})`, "success", 4000);
                }

                // Immediate stream refresh
                await fetchAttendance();
            } else if (res.status === 409) {
                // HTTP 409: Duplicate Attendance OR No Active Session
                const errorType = (res.data && res.data.error) || "";
                if (errorType === "No Active Session") {
                    updateScanFeedback("no-session", "No Active Class Session", res.data.message || "Attendance cannot be recorded without an active lecture session.", {
                        message: "Go to Class Sessions to activate a session first.",
                    });
                } else {
                    const att = (res.data && res.data.attendance) || {};
                    const stu = (res.data && res.data.student) || {};
                    const rollNo = stu.roll_number || att.roll_number;
                    const stuName = stu.student_name || att.student_name;

                    updateScanFeedback("duplicate", "DUPLICATE ATTENDANCE DETECTED", res.data.message || "Attendance has already been recorded for this student in this session.", {
                        rollNumber: rollNo,
                        studentName: stuName,
                        attendanceStatus: "ALREADY PRESENT",
                        scanTime: att.scanned_at,
                        courseName: (activeSession ? activeSession.course_name : (att.course_name || "--")),
                        message: "Card scan rejected: Student already marked Present.",
                    });
                }

                if (window.showToast) {
                    window.showToast(res.data.message || "Scan rejected by system.", "warning", 4000);
                }
            } else if (res.status === 404) {
                // HTTP 404: Unknown RFID Card
                updateScanFeedback("unregistered", "Unregistered RFID Card", res.data.message || "This RFID card is not registered in the system.", {
                    message: `UID: ${normalizedUid} - Card not assigned to any student.`,
                });

                if (window.showToast) {
                    window.showToast("Unregistered RFID card rejected.", "error", 4000);
                }
            } else if (res.status === 403) {
                // HTTP 403: Inactive Student
                updateScanFeedback("inactive", "Inactive Student Account", res.data.message || "This student account is inactive.", {
                    message: `UID: ${normalizedUid} - Inactive accounts cannot record attendance.`,
                });

                if (window.showToast) {
                    window.showToast("Inactive student card rejected.", "warning", 4000);
                }
            } else {
                // Generic API error (400, 500, etc.)
                updateScanFeedback("error", "Scan Error", res.message || "An error occurred while communicating with the backend.", {
                    message: res.message || "Request failed.",
                });

                if (window.showToast) {
                    window.showToast(res.message || "Scan error.", "error", 4000);
                }
            }
        } catch (err) {
            console.error("Simulator scan error:", err);
            updateScanFeedback("error", "Network / Server Error", "Could not connect to the backend server. Please verify Flask is running.");
        } finally {
            isSubmittingScan = false;
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = `<span class="btn-icon">&#x26A1;</span> Simulate Scan`;
            }
        }
    }

    // ==========================================================================
    // 7. AUTOMATIC STREAM POLLING COORDINATOR
    // ==========================================================================
    async function executeStreamPoll() {
        if (!isPollingActive || isPollInFlight) return;
        isPollInFlight = true;

        try {
            await fetchActiveSession();
            await fetchAttendance();
        } catch (err) {
            console.error("Stream poll cycle error:", err);
        } finally {
            isPollInFlight = false;
        }
    }

    function startPolling() {
        isPollingActive = true;
        if (pollingTimerId) clearInterval(pollingTimerId);

        updatePollingIndicatorUI(true);

        pollingTimerId = setInterval(() => {
            if (!document.hidden && isPollingActive) {
                executeStreamPoll();
            }
        }, POLLING_INTERVAL_MS);
    }

    function stopPolling() {
        isPollingActive = false;
        if (pollingTimerId) {
            clearInterval(pollingTimerId);
            pollingTimerId = null;
        }
        updatePollingIndicatorUI(false);
    }

    function togglePolling() {
        if (isPollingActive) {
            stopPolling();
            if (window.showToast) window.showToast("Live attendance stream paused.", "info", 2000);
        } else {
            startPolling();
            executeStreamPoll();
            if (window.showToast) window.showToast("Live attendance stream resumed.", "success", 2000);
        }
    }

    function updatePollingIndicatorUI(active) {
        const indicator = document.getElementById("polling-indicator");
        const statusText = document.getElementById("polling-status-text");
        const toggleBtn = document.getElementById("btn-toggle-polling");
        const toggleIcon = document.getElementById("btn-toggle-icon");
        const toggleText = document.getElementById("btn-toggle-text");
        const streamBadge = document.getElementById("stream-status-badge");

        if (indicator) {
            if (active) {
                indicator.classList.remove("paused");
                if (statusText) statusText.textContent = "Live Polling (4s)";
            } else {
                indicator.classList.add("paused");
                if (statusText) statusText.textContent = "Stream Paused";
            }
        }

        if (streamBadge) {
            if (active) {
                streamBadge.className = "badge badge-success";
                streamBadge.textContent = "Live Connected";
            } else {
                streamBadge.className = "badge badge-neutral";
                streamBadge.textContent = "Paused";
            }
        }

        if (toggleBtn && toggleText && toggleIcon) {
            if (active) {
                toggleIcon.innerHTML = "&#x23F8;";
                toggleText.textContent = "Pause Stream";
            } else {
                toggleIcon.innerHTML = "&#x25B6;";
                toggleText.textContent = "Resume Stream";
            }
        }
    }

    // ==========================================================================
    // 8. EVENT LISTENERS INITIALIZATION
    // ==========================================================================
    function initEventListeners() {
        // Form submit
        const form = document.getElementById("form-simulate-scan");
        if (form) {
            form.addEventListener("submit", handleSimulateScan);
        }

        // Form clear button
        const resetBtn = document.getElementById("btn-reset-scan");
        if (resetBtn) {
            resetBtn.addEventListener("click", () => {
                const uidInput = document.getElementById("sim-card-uid");
                const select = document.getElementById("sim-student-select");
                if (uidInput) uidInput.value = "";
                if (select) select.value = "";
                updateScanFeedback("idle", "Waiting for RFID Scan...", "Ready to receive RFID card events.");
            });
        }

        // Quick student select dropdown
        const select = document.getElementById("sim-student-select");
        if (select) {
            select.addEventListener("change", (e) => {
                const uidInput = document.getElementById("sim-card-uid");
                const selectedUid = e.target.value;
                if (uidInput && selectedUid) {
                    uidInput.value = selectedUid;
                }
            });
        }

        // Toggle Polling Button
        const toggleBtn = document.getElementById("btn-toggle-polling");
        if (toggleBtn) {
            toggleBtn.addEventListener("click", togglePolling);
        }

        // Manual Refresh Button
        const refreshBtn = document.getElementById("btn-refresh-stream");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", async () => {
                await fetchActiveSession();
                await fetchAttendance();
                if (window.showToast) window.showToast("Attendance stream refreshed.", "success", 2000);
            });
        }

        // Quick Test Chip: Unknown UID
        const chipUnregistered = document.getElementById("btn-chip-unregistered");
        if (chipUnregistered) {
            chipUnregistered.addEventListener("click", () => {
                const uidInput = document.getElementById("sim-card-uid");
                if (uidInput) {
                    uidInput.value = "UNKNOWN999";
                    handleSimulateScan();
                }
            });
        }

        // Quick Test Chip: Rescan / Duplicate
        const chipRescan = document.getElementById("btn-chip-rescan");
        if (chipRescan) {
            chipRescan.addEventListener("click", () => {
                const uidInput = document.getElementById("sim-card-uid");
                if (lastScannedUid) {
                    if (uidInput) uidInput.value = lastScannedUid;
                    handleSimulateScan();
                } else {
                    if (window.showToast) {
                        window.showToast("No previous scan recorded to duplicate yet.", "info", 3000);
                    }
                }
            });
        }

        // Pause polling when browser tab is inactive
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) {
                // Tab hidden: do not poll unnecessarily
            } else if (isPollingActive) {
                // Tab returned: immediately sync and refresh
                executeStreamPoll();
            }
        });
    }

    // ==========================================================================
    // 9. BOOTSTRAP INITIALIZATION
    // ==========================================================================
    document.addEventListener("DOMContentLoaded", async () => {
        initEventListeners();
        await fetchStudents();
        await fetchActiveSession();
        await fetchAttendance();
        startPolling();
    });

    // Expose controller for testing
    window.AttendanceController = {
        fetchActiveSession,
        fetchStudents,
        fetchAttendance,
        handleSimulateScan,
        updateScanFeedback,
        togglePolling,
        isActiveStudent,
    };
})();

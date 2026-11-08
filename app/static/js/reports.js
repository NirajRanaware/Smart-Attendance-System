/**
 * Smart Attendance Management System - Reports Controller (reports.js)
 * Manages daily attendance analytics, session breakdowns, student-wise logs,
 * search filtering, and official CSV report downloads.
 */

(function () {
    "use strict";

    // ==========================================================================
    // 1. STATE & UTILITIES
    // ==========================================================================
    let selectedDate = "";
    let allStudents = [];
    let dailyReportData = null;
    let dailyAttendanceRecords = [];
    let detailedRoster = [];
    let currentQuery = "";
    let currentSessionFilter = "all";
    let currentStatusFilter = "all";
    let isFetchingReport = false;
    let isDownloadingCsv = false;
    let latestRequestId = 0;

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
     * Resolves today's date formatted as YYYY-MM-DD in the Asia/Kolkata timezone.
     */
    function getTodayKolkataDate() {
        if (window.AppUtils && typeof window.AppUtils.getKolkataTodayDateString === "function") {
            return window.AppUtils.getKolkataTodayDateString();
        }
        return new Date().toISOString().split("T")[0];
    }

    /**
     * Formats a YYYY-MM-DD date string into a user-friendly display string.
     */
    function formatDateDisplay(dateStr) {
        if (!dateStr) return "--";
        try {
            const parts = dateStr.split("-");
            if (parts.length === 3) {
                const year = parseInt(parts[0], 10);
                const month = parseInt(parts[1], 10) - 1;
                const day = parseInt(parts[2], 10);
                const date = new Date(year, month, day);
                return new Intl.DateTimeFormat("en-US", {
                    weekday: "short",
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                }).format(date);
            }
            return dateStr;
        } catch {
            return dateStr;
        }
    }

    // ==========================================================================
    // 2. DATA FETCHING: STUDENTS, REPORT SUMMARY, AND ATTENDANCE
    // ==========================================================================
    async function fetchReportData(silent = false) {
        if (isFetchingReport) return;
        isFetchingReport = true;

        const thisRequestId = ++latestRequestId;

        // UI state elements
        const statTotal = document.getElementById("stat-total-students");
        const statActive = document.getElementById("stat-active-students");
        const statPresent = document.getElementById("stat-total-present");
        const statAbsent = document.getElementById("stat-total-absent");
        const statPercentage = document.getElementById("stat-attendance-percentage");

        const sessionBadge = document.getElementById("session-count-badge");
        const recordsBadge = document.getElementById("records-count-badge");
        const sessionTable = document.getElementById("session-breakdown-table");
        const sessionEmptyState = document.getElementById("session-empty-state");
        const recordsTable = document.getElementById("reports-attendance-table");
        const recordsErrorState = document.getElementById("records-error-state");
        const recordsNoSessionsState = document.getElementById("records-no-sessions-state");

        if (sessionBadge) sessionBadge.textContent = "Loading...";
        if (recordsBadge) recordsBadge.textContent = "Loading...";

        try {
            // Concurrent requests: Report summary, Attendance records, and Student roster (if not cached)
            const promises = [
                window.ApiClient.get(`/api/reports/daily?date=${encodeURIComponent(selectedDate)}`),
                window.ApiClient.get(`/api/attendance?date=${encodeURIComponent(selectedDate)}`),
            ];

            if (allStudents.length === 0) {
                promises.push(window.ApiClient.get("/api/students"));
            }

            const results = await Promise.all(promises);

            // Stale response guard
            if (thisRequestId !== latestRequestId) return;

            const reportRes = results[0];
            const attendanceRes = results[1];

            if (results.length > 2 && results[2].success && results[2].data) {
                allStudents = Array.isArray(results[2].data.students) ? results[2].data.students : [];
            }

            if (reportRes.success && reportRes.data) {
                if (recordsErrorState) recordsErrorState.style.display = "none";

                dailyReportData = reportRes.data;
                dailyAttendanceRecords = (attendanceRes.success && attendanceRes.data && Array.isArray(attendanceRes.data.attendance))
                    ? attendanceRes.data.attendance
                    : [];

                // 1. Update 5 KPI metric cards
                updateSummaryKPIs();

                // 2. Render Session Breakdown Table
                renderSessionBreakdown();

                // 3. Build Detailed Student Roster & Render Table
                buildDetailedRoster();
                populateSessionFilterDropdown();
                renderDetailedRecordsTable();

                if (!silent && window.showToast) {
                    window.showToast(`Attendance report for ${selectedDate} loaded.`, "success", 2000);
                }
            } else {
                // API failure feedback
                if (sessionBadge) sessionBadge.textContent = "Error";
                if (recordsBadge) recordsBadge.textContent = "Error";
                if (sessionTable) sessionTable.style.display = "none";
                if (sessionEmptyState) sessionEmptyState.style.display = "flex";
                if (recordsTable) recordsTable.style.display = "none";
                if (recordsNoSessionsState) recordsNoSessionsState.style.display = "none";
                if (recordsErrorState) recordsErrorState.style.display = "flex";
            }
        } catch (err) {
            console.error("Failed to fetch reports:", err);
            if (thisRequestId !== latestRequestId) return;

            if (sessionBadge) sessionBadge.textContent = "Error";
            if (recordsBadge) recordsBadge.textContent = "Error";
            if (sessionTable) sessionTable.style.display = "none";
            if (sessionEmptyState) sessionEmptyState.style.display = "flex";
            if (recordsTable) recordsTable.style.display = "none";
            if (recordsNoSessionsState) recordsNoSessionsState.style.display = "none";
            if (recordsErrorState) recordsErrorState.style.display = "flex";
        } finally {
            isFetchingReport = false;
        }
    }

    // ==========================================================================
    // 3. SUMMARY KPI CARDS CONTROLLER
    // ==========================================================================
    function updateSummaryKPIs() {
        const statTotal = document.getElementById("stat-total-students");
        const statActive = document.getElementById("stat-active-students");
        const statPresent = document.getElementById("stat-total-present");
        const statAbsent = document.getElementById("stat-total-absent");
        const statPercentage = document.getElementById("stat-attendance-percentage");

        const totalRegistered = allStudents.length;
        const eligibleActive = allStudents.filter(isActiveStudent).length;

        if (statTotal) statTotal.textContent = totalRegistered;
        if (statActive) statActive.textContent = eligibleActive;

        if (dailyReportData && dailyReportData.summary) {
            const summary = dailyReportData.summary;
            if (statPresent) statPresent.textContent = summary.total_present;
            if (statAbsent) statAbsent.textContent = summary.total_absent;
            if (statPercentage) statPercentage.textContent = `${summary.overall_attendance_percentage}%`;
        } else {
            if (statPresent) statPresent.textContent = "--";
            if (statAbsent) statAbsent.textContent = "--";
            if (statPercentage) statPercentage.textContent = "--%";
        }
    }

    // ==========================================================================
    // 4. SESSION-WISE BREAKDOWN TABLE
    // ==========================================================================
    function renderSessionBreakdown() {
        const table = document.getElementById("session-breakdown-table");
        const tbody = document.getElementById("session-breakdown-body");
        const emptyState = document.getElementById("session-empty-state");
        const badge = document.getElementById("session-count-badge");
        if (!tbody || !table) return;

        const sessions = (dailyReportData && Array.isArray(dailyReportData.sessions)) ? dailyReportData.sessions : [];

        if (sessions.length === 0) {
            table.style.display = "none";
            if (emptyState) emptyState.style.display = "flex";
            if (badge) badge.textContent = "0 Sessions";
            return;
        }

        if (emptyState) emptyState.style.display = "none";
        table.style.display = "table";
        if (badge) badge.textContent = `${sessions.length} Session${sessions.length === 1 ? "" : "s"}`;

        tbody.innerHTML = sessions
            .map((s) => {
                const safeId = escapeHtml(s.session_id);
                const safeCourse = escapeHtml(s.course_name);
                const safeTime = escapeHtml(s.start_time);
                const eligible = s.total_students;
                const present = s.present;
                const absent = s.absent;
                const pct = s.attendance_percentage;

                let progressColor = "#10b981"; // Green
                if (pct < 50) progressColor = "#ef4444"; // Red
                else if (pct < 75) progressColor = "#f59e0b"; // Amber

                return `
                    <tr>
                        <td class="cell-mono">#${safeId}</td>
                        <td class="cell-bold">${safeCourse}</td>
                        <td class="cell-mono">${safeTime}</td>
                        <td class="cell-mono" style="text-align: center;">${eligible}</td>
                        <td class="cell-mono text-success" style="text-align: center; font-weight: 700;">${present}</td>
                        <td class="cell-mono text-danger" style="text-align: center; font-weight: 700;">${absent}</td>
                        <td>
                            <div class="progress-container">
                                <div class="progress-bar-bg">
                                    <div class="progress-bar-fill" style="width: ${pct}%; background-color: ${progressColor};"></div>
                                </div>
                                <span class="progress-label">${pct}%</span>
                            </div>
                        </td>
                    </tr>
                `;
            })
            .join("");
    }

    // ==========================================================================
    // 5. DETAILED STUDENT ATTENDANCE ROSTER (STUDENT-WISE & SESSION-WISE)
    // ==========================================================================
    function buildDetailedRoster() {
        detailedRoster = [];
        const sessions = (dailyReportData && Array.isArray(dailyReportData.sessions)) ? dailyReportData.sessions : [];

        if (sessions.length === 0) {
            return;
        }

        // Active students only (matches backend CSV LEFT JOIN logic)
        const activeStudentsList = allStudents.filter(isActiveStudent);

        // Map scanned records by `${student_id}_${session_id}` and `${roll_number}_${session_id}`
        const attendanceMap = new Map();
        dailyAttendanceRecords.forEach((att) => {
            const rollKey = `${(att.roll_number || "").toUpperCase()}_${att.session_id}`;
            attendanceMap.set(rollKey, att);
        });

        // Construct roster per session
        sessions.forEach((sess) => {
            activeStudentsList.forEach((student) => {
                const rollKey = `${(student.roll_number || "").toUpperCase()}_${sess.session_id}`;
                const scanRecord = attendanceMap.get(rollKey);

                if (scanRecord) {
                    detailedRoster.push({
                        roll_number: student.roll_number,
                        student_name: student.full_name,
                        course_name: sess.course_name,
                        session_id: sess.session_id,
                        session_date: selectedDate,
                        scanned_at: scanRecord.scanned_at,
                        status: "Present",
                        source: scanRecord.source || "RFID_READER",
                    });
                } else {
                    detailedRoster.push({
                        roll_number: student.roll_number,
                        student_name: student.full_name,
                        course_name: sess.course_name,
                        session_id: sess.session_id,
                        session_date: selectedDate,
                        scanned_at: null,
                        status: "Absent",
                        source: "--",
                    });
                }
            });
        });
    }

    function populateSessionFilterDropdown() {
        const select = document.getElementById("filter-session");
        if (!select) return;

        const previousVal = select.value;
        const sessions = (dailyReportData && Array.isArray(dailyReportData.sessions)) ? dailyReportData.sessions : [];

        const options = ['<option value="all">All Sessions</option>'];
        sessions.forEach((s) => {
            const safeId = escapeHtml(s.session_id);
            const label = `#${safeId}: ${escapeHtml(s.course_name)} (${escapeHtml(s.start_time)})`;
            options.push(`<option value="${safeId}">${label}</option>`);
        });

        select.innerHTML = options.join("");
        if (previousVal && sessions.some((s) => String(s.session_id) === previousVal)) {
            select.value = previousVal;
        } else {
            select.value = "all";
            currentSessionFilter = "all";
        }
    }

    function renderDetailedRecordsTable() {
        const table = document.getElementById("reports-attendance-table");
        const tbody = document.getElementById("reports-attendance-body");
        const searchEmptyState = document.getElementById("records-empty-state");
        const noSessionsState = document.getElementById("records-no-sessions-state");
        const errorState = document.getElementById("records-error-state");
        const badge = document.getElementById("records-count-badge");
        if (!tbody || !table) return;

        if (errorState) errorState.style.display = "none";

        const sessions = (dailyReportData && Array.isArray(dailyReportData.sessions)) ? dailyReportData.sessions : [];

        // If no sessions on this date
        if (sessions.length === 0) {
            table.style.display = "none";
            if (searchEmptyState) searchEmptyState.style.display = "none";
            if (noSessionsState) noSessionsState.style.display = "flex";
            if (badge) badge.textContent = "0 Records";
            return;
        }

        if (noSessionsState) noSessionsState.style.display = "none";

        // Filter roster by search query, session filter, and status filter
        const filtered = detailedRoster.filter((row) => {
            // 1. Session Filter
            if (currentSessionFilter !== "all" && String(row.session_id) !== currentSessionFilter) {
                return false;
            }

            // 2. Status Filter
            if (currentStatusFilter !== "all" && row.status !== currentStatusFilter) {
                return false;
            }

            // 3. Search Query
            if (currentQuery) {
                const q = currentQuery.toLowerCase();
                const roll = (row.roll_number || "").toLowerCase();
                const name = (row.student_name || "").toLowerCase();
                if (!roll.includes(q) && !name.includes(q)) {
                    return false;
                }
            }

            return true;
        });

        // Update records count badge
        if (badge) {
            if (currentQuery || currentSessionFilter !== "all" || currentStatusFilter !== "all") {
                badge.textContent = `Showing ${filtered.length} of ${detailedRoster.length}`;
            } else {
                badge.textContent = `${detailedRoster.length} Records`;
            }
        }

        // If filtered results are empty
        if (filtered.length === 0) {
            table.style.display = "none";
            if (searchEmptyState) searchEmptyState.style.display = "flex";
            return;
        }

        if (searchEmptyState) searchEmptyState.style.display = "none";
        table.style.display = "table";

        tbody.innerHTML = filtered
            .map((rec, index) => {
                const rowNum = index + 1;
                const safeRoll = escapeHtml(rec.roll_number);
                const safeName = escapeHtml(rec.student_name);
                const safeCourse = escapeHtml(rec.course_name);
                const safeSessId = escapeHtml(rec.session_id);
                const safeDate = escapeHtml(rec.session_date);
                const safeSource = escapeHtml(rec.source);

                const statusBadge = rec.status === "Present"
                    ? `<span class="badge badge-success">Present</span>`
                    : `<span class="badge badge-absent">Absent</span>`;

                const timeDisplay = rec.status === "Present" && rec.scanned_at
                    ? (window.AppUtils && typeof window.AppUtils.formatScanTime === "function"
                        ? window.AppUtils.formatScanTime(rec.scanned_at)
                        : escapeHtml(rec.scanned_at))
                    : "--";

                return `
                    <tr>
                        <td class="cell-mono">${rowNum}</td>
                        <td class="cell-mono cell-bold">${safeRoll}</td>
                        <td class="cell-bold">${safeName}</td>
                        <td>${safeCourse}</td>
                        <td class="cell-mono" style="text-align: center;">#${safeSessId}</td>
                        <td class="cell-mono">${safeDate}</td>
                        <td class="cell-mono">${timeDisplay}</td>
                        <td>${statusBadge}</td>
                        <td><span class="cell-mono">${safeSource}</span></td>
                    </tr>
                `;
            })
            .join("");
    }

    // ==========================================================================
    // 6. CSV EXPORT HANDLER (GET /api/reports/export.csv?date=...)
    // ==========================================================================
    async function handleDownloadCsv() {
        if (isDownloadingCsv) return;
        isDownloadingCsv = true;

        const downloadBtn = document.getElementById("btn-download-csv");
        if (downloadBtn) {
            downloadBtn.disabled = true;
            downloadBtn.innerHTML = `<span class="btn-icon">&#x231B;</span> Generating CSV...`;
        }

        try {
            const url = `/api/reports/export.csv?date=${encodeURIComponent(selectedDate)}`;
            const response = await fetch(url);

            if (!response.ok) {
                let errorMsg = `Server error ${response.status}`;
                try {
                    const errorJson = await response.json();
                    errorMsg = errorJson.message || errorMsg;
                } catch {
                    // Ignore parsing error
                }
                if (window.showToast) {
                    window.showToast(`CSV Export Failed: ${errorMsg}`, "error", 5000);
                }
                return;
            }

            // Extract filename from Content-Disposition header if present
            let filename = `attendance_${selectedDate}.csv`;
            const disposition = response.headers.get("content-disposition");
            if (disposition && disposition.includes("filename=")) {
                const match = disposition.match(/filename=["']?([^"';]+)["']?/);
                if (match && match[1]) {
                    filename = match[1];
                }
            }

            // Retrieve binary blob and trigger browser download
            const blob = await response.blob();
            const downloadUrl = window.URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.style.display = "none";
            a.href = downloadUrl;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(downloadUrl);

            if (window.showToast) {
                window.showToast(`CSV Report "${filename}" downloaded successfully.`, "success", 4000);
            }
        } catch (err) {
            console.error("CSV Download error:", err);
            if (window.showToast) {
                window.showToast("Network error while downloading CSV report.", "error", 4000);
            }
        } finally {
            isDownloadingCsv = false;
            if (downloadBtn) {
                downloadBtn.disabled = false;
                downloadBtn.innerHTML = `<span class="btn-icon">&#x2193;</span> Download CSV Report`;
            }
        }
    }

    // ==========================================================================
    // 7. DATE CONTROLS & NAVIGATION
    // ==========================================================================
    function setDate(newDate) {
        if (!newDate || !/^\d{4}-\d{2}-\d{2}$/.test(newDate)) return;

        selectedDate = newDate;
        const dateInput = document.getElementById("report-date");
        const displayBadge = document.getElementById("display-selected-date");

        if (dateInput) dateInput.value = selectedDate;
        if (displayBadge) displayBadge.textContent = formatDateDisplay(selectedDate);

        // Fetch fresh report
        fetchReportData();
    }

    function shiftDate(days) {
        try {
            const parts = selectedDate.split("-");
            if (parts.length === 3) {
                const year = parseInt(parts[0], 10);
                const month = parseInt(parts[1], 10) - 1;
                const day = parseInt(parts[2], 10);
                const date = new Date(year, month, day);
                date.setDate(date.getDate() + days);

                const y = date.getFullYear();
                const m = String(date.getMonth() + 1).padStart(2, "0");
                const d = String(date.getDate()).padStart(2, "0");
                setDate(`${y}-${m}-${d}`);
            }
        } catch (err) {
            console.error("Failed to shift date:", err);
        }
    }

    // ==========================================================================
    // 8. EVENT LISTENERS INITIALIZATION
    // ==========================================================================
    function initEventListeners() {
        // Date Input
        const dateInput = document.getElementById("report-date");
        if (dateInput) {
            dateInput.addEventListener("change", (e) => {
                const val = e.target.value;
                if (val && val !== selectedDate) {
                    setDate(val);
                }
            });
        }

        // Quick Date Buttons
        const btnToday = document.getElementById("btn-date-today");
        if (btnToday) {
            btnToday.addEventListener("click", () => setDate(getTodayKolkataDate()));
        }

        const btnPrev = document.getElementById("btn-date-prev");
        if (btnPrev) {
            btnPrev.addEventListener("click", () => shiftDate(-1));
        }

        const btnNext = document.getElementById("btn-date-next");
        if (btnNext) {
            btnNext.addEventListener("click", () => shiftDate(1));
        }

        // Refresh Button
        const refreshBtn = document.getElementById("btn-refresh-report");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", () => {
                fetchReportData(false);
            });
        }

        // CSV Download Button
        const downloadBtn = document.getElementById("btn-download-csv");
        if (downloadBtn) {
            downloadBtn.addEventListener("click", handleDownloadCsv);
        }

        // Live Search Input
        const searchInput = document.getElementById("search-records");
        const clearSearchBtn = document.getElementById("btn-clear-search");

        if (searchInput) {
            searchInput.addEventListener("input", (e) => {
                currentQuery = e.target.value.trim();
                if (clearSearchBtn) {
                    clearSearchBtn.style.display = currentQuery ? "block" : "none";
                }
                renderDetailedRecordsTable();
            });
        }

        if (clearSearchBtn) {
            clearSearchBtn.addEventListener("click", () => {
                if (searchInput) {
                    searchInput.value = "";
                    searchInput.focus();
                }
                currentQuery = "";
                clearSearchBtn.style.display = "none";
                renderDetailedRecordsTable();
            });
        }

        // Session Filter Dropdown
        const sessionFilter = document.getElementById("filter-session");
        if (sessionFilter) {
            sessionFilter.addEventListener("change", (e) => {
                currentSessionFilter = e.target.value;
                renderDetailedRecordsTable();
            });
        }

        // Status Filter Dropdown
        const statusFilter = document.getElementById("filter-status");
        if (statusFilter) {
            statusFilter.addEventListener("change", (e) => {
                currentStatusFilter = e.target.value;
                renderDetailedRecordsTable();
            });
        }
    }

    // ==========================================================================
    // 9. BOOTSTRAP INITIALIZATION
    // ==========================================================================
    document.addEventListener("DOMContentLoaded", async () => {
        initEventListeners();

        // Initialize with Asia/Kolkata date
        const initialDate = getTodayKolkataDate();
        selectedDate = initialDate;

        const dateInput = document.getElementById("report-date");
        const displayBadge = document.getElementById("display-selected-date");
        if (dateInput) dateInput.value = initialDate;
        if (displayBadge) displayBadge.textContent = formatDateDisplay(initialDate);

        await fetchReportData(true);
    });

    // Expose controller for testing
    window.ReportsController = {
        fetchReportData,
        handleDownloadCsv,
        setDate,
        shiftDate,
        updateSummaryKPIs,
        renderSessionBreakdown,
        renderDetailedRecordsTable,
        isActiveStudent,
    };
})();

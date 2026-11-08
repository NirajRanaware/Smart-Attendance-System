/**
 * Smart Attendance Management System - Class Session Management Controller (sessions.js)
 * Manages class session creation, session roster listing, active session monitoring,
 * and immediate atomic session activation/deactivation via Flask REST APIs.
 */

(function () {
    "use strict";

    // ==========================================================================
    // 1. STATE & UTILITIES
    // ==========================================================================
    let allSessions = [];
    let activeSession = null;
    let currentQuery = "";
    let currentFilter = "all";
    let isFetchingSessions = false;
    let isFetchingActive = false;
    let isSubmitting = false;
    let isMutating = false;

    // Defensive active normalization (handles booleans and SQLite integers 1 / 0)
    const isActiveSession = (s) => Boolean(s) && (s.active === true || Number(s.active) === 1);

    // Escape HTML to prevent injection
    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // Display inline form feedback (error or success banner)
    function setFormAlert(message, type = "error") {
        const alertEl = document.getElementById("session-form-alert");
        const iconEl = document.getElementById("session-form-alert-icon");
        const textEl = document.getElementById("session-form-alert-text");
        if (!alertEl || !textEl) return;

        if (!message) {
            alertEl.style.display = "none";
            return;
        }

        alertEl.className = `form-alert alert-${type}`;
        if (iconEl) {
            iconEl.innerHTML = type === "success" ? "&#x2705;" : "&#x26A0;&#xFE0F;";
        }
        textEl.textContent = message;
        alertEl.style.display = "flex";
    }

    function clearFormAlert() {
        setFormAlert(null);
    }

    /**
     * Sets default date and time values for the session creation form.
     */
    function resetFormDefaults() {
        const dateInput = document.getElementById("session-date");
        const timeInput = document.getElementById("session-start-time");
        const endTimeInput = document.getElementById("session-end-time");
        const courseInput = document.getElementById("session-course-name");

        if (courseInput) {
            courseInput.value = "";
        }

        if (dateInput) {
            if (window.AppUtils && typeof window.AppUtils.getKolkataTodayDateString === "function") {
                dateInput.value = window.AppUtils.getKolkataTodayDateString();
            } else {
                const now = new Date();
                dateInput.value = now.toISOString().split("T")[0];
            }
        }

        let defaultStartTime = "09:00";
        let defaultEndTime = "10:00";

        try {
            const now = new Date();
            const kolkataHours = new Intl.DateTimeFormat("en-GB", {
                timeZone: "Asia/Kolkata",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
            }).format(now);
            defaultStartTime = kolkataHours;

            // Add 1 hour for default end time
            const [h, m] = kolkataHours.split(":").map(Number);
            const endH = h + 1;
            if (endH < 24) {
                defaultEndTime = `${String(endH).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
            } else {
                defaultEndTime = "23:59";
            }
        } catch {
            defaultStartTime = "09:00";
            defaultEndTime = "10:00";
        }

        if (timeInput) {
            timeInput.value = defaultStartTime;
        }

        if (endTimeInput) {
            endTimeInput.value = defaultEndTime;
        }

        clearFormAlert();
    }

    // ==========================================================================
    // 2. ACTIVE SESSION RETRIEVAL (GET /api/sessions/active)
    // ==========================================================================
    async function fetchActiveSession() {
        if (isFetchingActive) return;
        isFetchingActive = true;

        const heroCard = document.getElementById("active-session-card");
        const heroDot = document.getElementById("active-dot");
        const statusLabel = document.getElementById("active-status-label");
        const courseNameEl = document.getElementById("active-course-name");
        const sessionDateEl = document.getElementById("active-session-date");
        const startTimeEl = document.getElementById("active-start-time");
        const sessionIdEl = document.getElementById("active-session-id");
        const badgeEl = document.getElementById("active-session-badge");
        const btnDeactivateHero = document.getElementById("btn-deactivate-active-session");

        try {
            const res = await window.ApiClient.get("/api/sessions/active");

            if (res.success && res.data && res.data.session) {
                activeSession = res.data.session;

                if (heroCard) heroCard.classList.add("is-active");
                if (heroDot) heroDot.className = "status-dot status-dot-active";
                if (statusLabel) statusLabel.textContent = "CURRENT ACTIVE SESSION";
                if (courseNameEl) courseNameEl.textContent = activeSession.course_name;
                if (sessionDateEl) sessionDateEl.textContent = activeSession.session_date;
                if (startTimeEl) startTimeEl.textContent = activeSession.start_time;
                if (sessionIdEl) sessionIdEl.textContent = activeSession.id;

                const endTimeWrapper = document.getElementById("active-end-time-wrapper");
                const endTimeEl = document.getElementById("active-end-time");
                if (endTimeWrapper && endTimeEl) {
                    if (activeSession.end_time) {
                        endTimeEl.textContent = activeSession.end_time;
                        endTimeWrapper.style.display = "inline";
                    } else {
                        endTimeEl.textContent = "Not Set";
                        endTimeWrapper.style.display = "inline";
                    }
                }

                if (badgeEl) {
                    badgeEl.className = "badge badge-success";
                    badgeEl.textContent = "Active Now";
                }

                if (btnDeactivateHero) {
                    btnDeactivateHero.style.display = "inline-flex";
                    btnDeactivateHero.dataset.id = activeSession.id;
                    btnDeactivateHero.dataset.name = activeSession.course_name;
                }
            } else {
                activeSession = null;

                if (heroCard) heroCard.classList.remove("is-active");
                if (heroDot) heroDot.className = "status-dot";
                if (statusLabel) statusLabel.textContent = "NO ACTIVE SESSION";
                if (courseNameEl) courseNameEl.textContent = "No Active Class Session";
                if (sessionDateEl) sessionDateEl.textContent = "--";
                if (startTimeEl) startTimeEl.textContent = "--";
                if (sessionIdEl) sessionIdEl.textContent = "--";

                const endTimeWrapper = document.getElementById("active-end-time-wrapper");
                if (endTimeWrapper) {
                    endTimeWrapper.style.display = "none";
                }

                if (badgeEl) {
                    badgeEl.className = "badge badge-neutral";
                    badgeEl.textContent = "Inactive";
                }

                if (btnDeactivateHero) {
                    btnDeactivateHero.style.display = "none";
                }
            }

            // Also synchronize the topbar indicator
            if (typeof window.checkActiveSession === "function") {
                window.checkActiveSession();
            }
        } catch (err) {
            console.error("Failed to fetch active session:", err);
            if (courseNameEl) courseNameEl.textContent = "Unable to check active session";
        } finally {
            isFetchingActive = false;
        }
    }

    // ==========================================================================
    // 3. SESSION ROSTER RETRIEVAL (GET /api/sessions)
    // ==========================================================================
    async function fetchSessions(silent = false) {
        if (isFetchingSessions) return;
        isFetchingSessions = true;

        const badge = document.getElementById("session-count-badge");
        const table = document.getElementById("session-table");
        const tableEmptyState = document.getElementById("session-table-empty-state");
        const searchEmptyState = document.getElementById("session-search-empty-state");
        const tableErrorState = document.getElementById("session-table-error-state");

        if (badge) badge.textContent = "Loading...";

        try {
            const res = await window.ApiClient.get("/api/sessions");

            if (res.success && res.data) {
                allSessions = Array.isArray(res.data.sessions) ? res.data.sessions : [];

                if (tableErrorState) tableErrorState.style.display = "none";
                renderSessionTable();

                if (!silent && window.showToast && allSessions.length > 0) {
                    window.showToast("Class session roster updated.", "success", 2000);
                }
            } else {
                if (badge) badge.textContent = "Error";
                if (table) table.style.display = "none";
                if (tableEmptyState) tableEmptyState.style.display = "none";
                if (searchEmptyState) searchEmptyState.style.display = "none";
                if (tableErrorState) tableErrorState.style.display = "flex";
            }
        } catch (err) {
            console.error("Failed to fetch sessions:", err);
            if (badge) badge.textContent = "Error";
            if (table) table.style.display = "none";
            if (tableEmptyState) tableEmptyState.style.display = "none";
            if (searchEmptyState) searchEmptyState.style.display = "none";
            if (tableErrorState) tableErrorState.style.display = "flex";
        } finally {
            isFetchingSessions = false;
        }

        // Always ensure active session hero banner is up to date
        fetchActiveSession();
    }

    // ==========================================================================
    // 4. TABLE RENDERING, SEARCH & FILTERING
    // ==========================================================================
    function renderSessionTable() {
        const table = document.getElementById("session-table");
        const tbody = document.getElementById("session-table-body");
        const emptyState = document.getElementById("session-table-empty-state");
        const searchEmptyState = document.getElementById("session-search-empty-state");
        const errorState = document.getElementById("session-table-error-state");
        const badge = document.getElementById("session-count-badge");
        if (!tbody || !table) return;

        if (errorState) errorState.style.display = "none";

        // If entire database has no sessions
        if (allSessions.length === 0) {
            table.style.display = "none";
            if (emptyState) emptyState.style.display = "flex";
            if (searchEmptyState) searchEmptyState.style.display = "none";
            if (badge) badge.textContent = "0 Sessions";
            return;
        }

        // Apply Status Filter and Search Query
        const filtered = allSessions.filter((session) => {
            const active = isActiveSession(session);

            // 1. Status Filter
            if (currentFilter === "active" && !active) return false;
            if (currentFilter === "inactive" && active) return false;

            // 2. Search Query (Course Name, Session Date, Start/End Time, Session ID)
            if (currentQuery) {
                const q = currentQuery.toLowerCase();
                const course = (session.course_name || "").toLowerCase();
                const date = (session.session_date || "").toLowerCase();
                const start = (session.start_time || "").toLowerCase();
                const end = (session.end_time || "").toLowerCase();
                const idStr = String(session.id || "");

                if (!course.includes(q) && !date.includes(q) && !start.includes(q) && !end.includes(q) && !idStr.includes(q)) {
                    return false;
                }
            }

            return true;
        });

        // Update Session Count Badge
        if (badge) {
            if (currentQuery || currentFilter !== "all") {
                badge.textContent = `Showing ${filtered.length} of ${allSessions.length}`;
            } else {
                badge.textContent = `${allSessions.length} Session${allSessions.length === 1 ? "" : "s"}`;
            }
        }

        // If search/filter produces 0 results
        if (filtered.length === 0) {
            table.style.display = "none";
            if (emptyState) emptyState.style.display = "none";
            if (searchEmptyState) searchEmptyState.style.display = "flex";
            return;
        }

        // Render matching rows
        table.style.display = "table";
        if (emptyState) emptyState.style.display = "none";
        if (searchEmptyState) searchEmptyState.style.display = "none";

        tbody.innerHTML = filtered
            .map((s) => {
                const active = isActiveSession(s);
                const safeId = escapeHtml(s.id);
                const safeCourse = escapeHtml(s.course_name);
                const safeDate = escapeHtml(s.session_date);
                const safeTime = escapeHtml(s.start_time);
                const safeEndTime = s.end_time ? escapeHtml(s.end_time) : '<span class="text-muted">Not Set</span>';

                const rowClass = active ? "row-active-session" : "";

                const statusBadge = active
                    ? `<span class="badge badge-success"><span class="badge-dot"></span> Active</span>`
                    : `<span class="badge badge-neutral">Inactive</span>`;

                const actionButton = active
                    ? `<button type="button" class="btn-deactivate" data-action="deactivate" data-id="${safeId}" data-name="${safeCourse}" title="Deactivate session #${safeId}">
                           &#x23F9; Deactivate
                       </button>`
                    : `<button type="button" class="btn-activate" data-action="activate" data-id="${safeId}" data-name="${safeCourse}" title="Activate session #${safeId}">
                           &#x25B6; Activate
                       </button>`;

                return `
                    <tr class="${rowClass}">
                        <td class="cell-mono">#${safeId}</td>
                        <td class="cell-course">${safeCourse}</td>
                        <td class="cell-mono">${safeDate}</td>
                        <td class="cell-mono">${safeTime}</td>
                        <td class="cell-mono">${safeEndTime}</td>
                        <td>${statusBadge}</td>
                        <td class="cell-actions" style="text-align: center;">${actionButton}</td>
                    </tr>
                `;
            })
            .join("");
    }

    // ==========================================================================
    // 5. SESSION ACTIVATION & DEACTIVATION CONTROLS
    // ==========================================================================
    async function handleActivateSession(sessionId, courseName) {
        if (isMutating) return;

        const confirmMsg = `Activate Session #${sessionId}: "${courseName}"?\n\nNote: Any currently active session will be automatically deactivated in an immediate atomic transaction.`;
        if (!window.confirm(confirmMsg)) {
            return;
        }

        isMutating = true;
        setAllActionButtonsDisabled(true);

        try {
            const res = await window.ApiClient.patch(`/api/sessions/${sessionId}/activate`);

            if (res.success) {
                if (window.showToast) {
                    window.showToast(`Session #${sessionId} ("${courseName}") is now ACTIVE.`, "success", 4000);
                }
                // Refresh sessions and active session card
                await fetchSessions(true);
            } else {
                if (window.showToast) {
                    window.showToast(res.message || "Failed to activate class session.", "error", 5000);
                }
            }
        } catch (err) {
            console.error("Activation request failed:", err);
            if (window.showToast) {
                window.showToast("Network error while activating session.", "error", 4000);
            }
        } finally {
            isMutating = false;
            setAllActionButtonsDisabled(false);
        }
    }

    async function handleDeactivateSession(sessionId, courseName) {
        if (isMutating) return;

        const confirmMsg = `Deactivate Session #${sessionId}: "${courseName}"?\n\nNote: RFID attendance scans will be rejected while no session is active.`;
        if (!window.confirm(confirmMsg)) {
            return;
        }

        isMutating = true;
        setAllActionButtonsDisabled(true);

        try {
            const res = await window.ApiClient.patch(`/api/sessions/${sessionId}/deactivate`);

            if (res.success) {
                if (window.showToast) {
                    window.showToast(`Session #${sessionId} deactivated. No session is currently active.`, "info", 4000);
                }
                // Refresh sessions and active session card
                await fetchSessions(true);
            } else {
                if (window.showToast) {
                    window.showToast(res.message || "Failed to deactivate class session.", "error", 5000);
                }
            }
        } catch (err) {
            console.error("Deactivation request failed:", err);
            if (window.showToast) {
                window.showToast("Network error while deactivating session.", "error", 4000);
            }
        } finally {
            isMutating = false;
            setAllActionButtonsDisabled(false);
        }
    }

    function setAllActionButtonsDisabled(disabled) {
        const buttons = document.querySelectorAll(".btn-activate, .btn-deactivate");
        buttons.forEach((btn) => {
            btn.disabled = disabled;
            if (disabled) {
                btn.classList.add("btn-action-disabled");
            } else {
                btn.classList.remove("btn-action-disabled");
            }
        });
    }

    // ==========================================================================
    // 6. CREATE SESSION FORM SUBMISSION (POST /api/sessions)
    // ==========================================================================
    async function handleCreateSession(e) {
        e.preventDefault();
        if (isSubmitting) return;

        clearFormAlert();

        const courseInput = document.getElementById("session-course-name");
        const dateInput = document.getElementById("session-date");
        const timeInput = document.getElementById("session-start-time");
        const endTimeInput = document.getElementById("session-end-time");
        const submitBtn = document.getElementById("btn-submit-session");

        const courseName = (courseInput ? courseInput.value : "").trim();
        const sessionDate = (dateInput ? dateInput.value : "").trim();
        const startTime = (timeInput ? timeInput.value : "").trim();
        const endTime = (endTimeInput ? endTimeInput.value : "").trim();

        // Client-side validations adhering to backend requirements
        if (!courseName) {
            setFormAlert("Please enter a course name.", "error");
            if (courseInput) courseInput.focus();
            return;
        }

        if (courseName.length < 2 || courseName.length > 100) {
            setFormAlert("Course name must be between 2 and 100 characters.", "error");
            if (courseInput) courseInput.focus();
            return;
        }

        if (!sessionDate) {
            setFormAlert("Please select a session date.", "error");
            if (dateInput) dateInput.focus();
            return;
        }

        const dateRegex = /^\d{4}-\d{2}-\d{2}$/;
        if (!dateRegex.test(sessionDate)) {
            setFormAlert("Session date must be in YYYY-MM-DD format.", "error");
            if (dateInput) dateInput.focus();
            return;
        }

        if (!startTime) {
            setFormAlert("Please enter a session start time.", "error");
            if (timeInput) timeInput.focus();
            return;
        }

        const timeRegex = /^\d{2}:\d{2}$/;
        if (!timeRegex.test(startTime)) {
            setFormAlert("Start time must be in 24-hour HH:MM format (e.g. 10:00).", "error");
            if (timeInput) timeInput.focus();
            return;
        }

        if (!endTime) {
            setFormAlert("Please enter a session end time.", "error");
            if (endTimeInput) endTimeInput.focus();
            return;
        }

        if (!timeRegex.test(endTime)) {
            setFormAlert("End time must be in 24-hour HH:MM format (e.g. 11:00).", "error");
            if (endTimeInput) endTimeInput.focus();
            return;
        }

        if (endTime <= startTime) {
            setFormAlert("End time must be later than start time.", "error");
            if (endTimeInput) endTimeInput.focus();
            return;
        }

        // Prepare payload
        const payload = {
            course_name: courseName,
            session_date: sessionDate,
            start_time: startTime,
            end_time: endTime,
        };

        // Submit to API
        isSubmitting = true;
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<span class="btn-icon">&#x231B;</span> Creating Session...`;
        }

        try {
            const res = await window.ApiClient.post("/api/sessions", payload);

            if (res.success && res.status === 201) {
                // Success feedback
                const createdId = res.data && res.data.session ? res.data.session.id : "";
                if (window.showToast) {
                    window.showToast(`Session #${createdId} ("${courseName}") created successfully!`, "success", 4000);
                }

                // Reset form values to default date/time
                resetFormDefaults();

                // Refresh session roster table
                await fetchSessions(true);
            } else {
                // Failure feedback
                const errorMsg = res.message || "Failed to create class session. Please check your inputs.";
                setFormAlert(errorMsg, "error");
                // Intentionally keep user inputs so they can be corrected
            }
        } catch (err) {
            console.error("Session creation error:", err);
            setFormAlert("Network error while submitting session. Please try again.", "error");
        } finally {
            isSubmitting = false;
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = `<span class="btn-icon">&#x2795;</span> Create Session`;
            }
        }
    }

    // ==========================================================================
    // 7. EVENT LISTENERS INITIALIZATION
    // ==========================================================================
    function initEventListeners() {
        // Form submission
        const form = document.getElementById("form-create-session");
        if (form) {
            form.addEventListener("submit", handleCreateSession);
        }

        // Form reset button
        const resetBtn = document.getElementById("btn-reset-session-form");
        if (resetBtn) {
            resetBtn.addEventListener("click", () => {
                resetFormDefaults();
                clearFormAlert();
            });
        }

        // Header action: Scroll to create form
        const scrollBtn = document.getElementById("btn-scroll-to-create");
        if (scrollBtn) {
            scrollBtn.addEventListener("click", () => {
                const card = document.getElementById("session-create-card");
                if (card) {
                    card.scrollIntoView({ behavior: "smooth", block: "start" });
                    const input = document.getElementById("session-course-name");
                    if (input) input.focus();
                }
            });
        }

        // Header action: Refresh button
        const refreshBtn = document.getElementById("btn-refresh-sessions");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", () => {
                fetchSessions(false);
            });
        }

        // Hero Card Deactivate button
        const heroDeactivateBtn = document.getElementById("btn-deactivate-active-session");
        if (heroDeactivateBtn) {
            heroDeactivateBtn.addEventListener("click", () => {
                const id = heroDeactivateBtn.dataset.id;
                const name = heroDeactivateBtn.dataset.name || "Active Session";
                if (id) {
                    handleDeactivateSession(id, name);
                }
            });
        }

        // Table Action Buttons (Delegated event handling)
        const tbody = document.getElementById("session-table-body");
        if (tbody) {
            tbody.addEventListener("click", (e) => {
                const btn = e.target.closest("button[data-action]");
                if (!btn) return;

                const action = btn.dataset.action;
                const id = btn.dataset.id;
                const name = btn.dataset.name;

                if (action === "activate" && id) {
                    handleActivateSession(id, name);
                } else if (action === "deactivate" && id) {
                    handleDeactivateSession(id, name);
                }
            });
        }

        // Live Search Input
        const searchInput = document.getElementById("search-sessions");
        const clearSearchBtn = document.getElementById("btn-clear-session-search");

        if (searchInput) {
            searchInput.addEventListener("input", (e) => {
                currentQuery = e.target.value.trim();
                if (clearSearchBtn) {
                    clearSearchBtn.style.display = currentQuery ? "block" : "none";
                }
                renderSessionTable();
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
                renderSessionTable();
            });
        }

        // Status Filter Dropdown
        const filterSelect = document.getElementById("filter-session-status");
        if (filterSelect) {
            filterSelect.addEventListener("change", (e) => {
                currentFilter = e.target.value;
                renderSessionTable();
            });
        }
    }

    // ==========================================================================
    // 8. BOOTSTRAP INITIALIZATION
    // ==========================================================================
    document.addEventListener("DOMContentLoaded", () => {
        resetFormDefaults();
        initEventListeners();
        fetchActiveSession();
        fetchSessions(true);
    });

    // Expose test hooks for verification
    window.SessionsController = {
        fetchSessions,
        fetchActiveSession,
        handleActivateSession,
        handleDeactivateSession,
        isActiveSession,
    };
})();

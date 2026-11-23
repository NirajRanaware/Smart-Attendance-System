/**
 * Smart Attendance Management System - Settings & Database Administration (settings.js)
 * Fetches real-time database stats, manages typed confirmation modal, enforces
 * secondary confirmation, and securely triggers operational test data clearing.
 */

(function () {
    "use strict";

    // Expected exact confirmation phrase
    const REQUIRED_CONFIRM_PHRASE = "DELETE ALL DATA";

    // DOM Elements
    let statDbStudents = null;
    let statDbActiveStudents = null;
    let statDbSessions = null;
    let statDbAttendance = null;
    let statDbPath = null;
    let btnRefreshStats = null;

    // Google Sheets DOM Elements
    let btnRefreshSheetsSync = null;
    let sheetsRefreshIcon = null;
    let sheetsRefreshText = null;
    let sheetsSyncErrorBanner = null;
    let sheetsSyncErrorText = null;
    let sheetsConfigSyncStatus = null;
    let sheetsConfigCredsStatus = null;
    let sheetsConfigSheetStatus = null;
    let sheetsLastSyncTime = null;
    let sheetsMetricPending = null;
    let sheetsMetricSynced = null;
    let sheetsMetricInflight = null;
    let sheetsMetricDeadletter = null;
    let sheetsMetricFailed = null;
    let sheetsMetricTotal = null;
    let isFetchingSheets = false;

    // Modal DOM Elements
    let modalBackdrop = null;
    let btnOpenModal = null;
    let btnCloseModal = null;
    let btnCancelClear = null;
    let btnConfirmClear = null;
    let confirmInput = null;
    let modalErrorBanner = null;
    let modalErrorText = null;
    let confirmBtnText = null;
    let confirmBtnIcon = null;

    /**
     * Initializes DOM references and binds user interactions.
     */
    function init() {
        statDbStudents = document.getElementById("stat-db-students");
        statDbActiveStudents = document.getElementById("stat-db-active-students");
        statDbSessions = document.getElementById("stat-db-sessions");
        statDbAttendance = document.getElementById("stat-db-attendance");
        statDbPath = document.getElementById("stat-db-path");
        btnRefreshStats = document.getElementById("btn-refresh-stats");

        modalBackdrop = document.getElementById("modal-clear-backdrop");
        btnOpenModal = document.getElementById("btn-open-clear-modal");
        btnCloseModal = document.getElementById("btn-close-modal");
        btnCancelClear = document.getElementById("btn-cancel-clear");
        btnConfirmClear = document.getElementById("btn-confirm-clear");
        confirmInput = document.getElementById("clear-data-confirmation-input");
        modalErrorBanner = document.getElementById("modal-error-banner");
        modalErrorText = document.getElementById("modal-error-text");
        confirmBtnText = document.getElementById("confirm-btn-text");
        confirmBtnIcon = document.getElementById("confirm-btn-icon");

        // Google Sheets DOM references
        btnRefreshSheetsSync = document.getElementById("btn-refresh-sheets-sync");
        sheetsRefreshIcon = document.getElementById("sheets-refresh-icon");
        sheetsRefreshText = document.getElementById("sheets-refresh-text");
        sheetsSyncErrorBanner = document.getElementById("sheets-sync-error-banner");
        sheetsSyncErrorText = document.getElementById("sheets-sync-error-text");
        sheetsConfigSyncStatus = document.getElementById("sheets-config-sync-status");
        sheetsConfigCredsStatus = document.getElementById("sheets-config-creds-status");
        sheetsConfigSheetStatus = document.getElementById("sheets-config-sheet-status");
        sheetsLastSyncTime = document.getElementById("sheets-last-sync-time");
        sheetsMetricPending = document.getElementById("sheets-metric-pending");
        sheetsMetricSynced = document.getElementById("sheets-metric-synced");
        sheetsMetricInflight = document.getElementById("sheets-metric-inflight");
        sheetsMetricDeadletter = document.getElementById("sheets-metric-deadletter");
        sheetsMetricFailed = document.getElementById("sheets-metric-failed");
        sheetsMetricTotal = document.getElementById("sheets-metric-total");

        // Event Listeners
        if (btnRefreshStats) {
            btnRefreshStats.addEventListener("click", () => {
                fetchDatabaseStats(true);
            });
        }

        if (btnRefreshSheetsSync) {
            btnRefreshSheetsSync.addEventListener("click", () => {
                fetchSheetsSyncStatus(true);
            });
        }

        if (btnOpenModal) {
            btnOpenModal.addEventListener("click", openClearModal);
        }

        if (btnCloseModal) {
            btnCloseModal.addEventListener("click", closeClearModal);
        }

        if (btnCancelClear) {
            btnCancelClear.addEventListener("click", closeClearModal);
        }

        // Close modal on clicking backdrop outside dialog
        if (modalBackdrop) {
            modalBackdrop.addEventListener("click", (e) => {
                if (e.target === modalBackdrop) {
                    closeClearModal();
                }
            });
        }

        // Close on Escape key
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && modalBackdrop && modalBackdrop.style.display !== "none") {
                closeClearModal();
            }
        });

        // Typed confirmation input listener
        if (confirmInput) {
            confirmInput.addEventListener("input", handlePhraseInput);
        }

        // Final deletion button listener
        if (btnConfirmClear) {
            btnConfirmClear.addEventListener("click", handleClearDataSubmit);
        }

        // Initial fetch of database stats and sheets status
        fetchDatabaseStats(false);
        fetchSheetsSyncStatus(false);
    }

    /**
     * Fetches live database statistics from GET /api/admin/database-stats.
     * Updates KPI metric cards and database file path display.
     * @param {boolean} showFeedback - Whether to show a toast message upon completion
     */
    async function fetchDatabaseStats(showFeedback = false) {
        if (btnRefreshStats) {
            btnRefreshStats.disabled = true;
            btnRefreshStats.classList.add("loading");
        }

        try {
            const res = await ApiClient.get("/api/admin/database-stats");
            if (res.success && res.data && res.data.data) {
                const stats = res.data.data;
                if (statDbStudents) statDbStudents.textContent = stats.total_students.toLocaleString();
                if (statDbActiveStudents) statDbActiveStudents.textContent = stats.active_students.toLocaleString();
                if (statDbSessions) statDbSessions.textContent = stats.total_sessions.toLocaleString();
                if (statDbAttendance) statDbAttendance.textContent = stats.total_attendance.toLocaleString();
                if (statDbPath) statDbPath.textContent = stats.database_path || "attendance.db";

                if (showFeedback && typeof window.showToast === "function") {
                    window.showToast("Database statistics updated successfully.", "success", 2500);
                }
            } else {
                if (statDbStudents) statDbStudents.textContent = "Error";
                if (statDbActiveStudents) statDbActiveStudents.textContent = "Error";
                if (statDbSessions) statDbSessions.textContent = "Error";
                if (statDbAttendance) statDbAttendance.textContent = "Error";
                if (showFeedback && typeof window.showToast === "function") {
                    window.showToast(res.message || "Failed to retrieve database stats.", "error");
                }
            }
        } catch (err) {
            if (showFeedback && typeof window.showToast === "function") {
                window.showToast(`Error connecting to database API: ${err.message}`, "error");
            }
        } finally {
            if (btnRefreshStats) {
                btnRefreshStats.disabled = false;
                btnRefreshStats.classList.remove("loading");
            }
        }
    }

    /**
     * Renders a badge HTML string based on boolean/null status.
     * @param {boolean|null} status - Current status flag
     * @param {string} trueLabel - Label when true
     * @param {string} falseLabel - Label when false
     * @param {string} trueClass - CSS badge class when true
     * @param {string} falseClass - CSS badge class when false
     * @returns {string} Safe HTML string for badge
     */
    function renderSheetsBadge(status, trueLabel, falseLabel, trueClass = "badge-success", falseClass = "badge-secondary") {
        if (status === true) {
            return `<span class="badge ${trueClass}">${trueLabel}</span>`;
        } else if (status === false) {
            return `<span class="badge ${falseClass}">${falseLabel}</span>`;
        } else {
            return `<span class="badge badge-warning">Unknown</span>`;
        }
    }

    /**
     * Fetches live Google Sheets synchronization status from GET /api/admin/sheets-sync/status.
     * Updates configuration status pills, last sync timestamp, and outbox queue metrics.
     * @param {boolean} showFeedback - Whether to show a toast message upon completion
     */
    async function fetchSheetsSyncStatus(showFeedback = false) {
        if (isFetchingSheets) return;
        isFetchingSheets = true;

        if (btnRefreshSheetsSync) {
            btnRefreshSheetsSync.disabled = true;
            btnRefreshSheetsSync.classList.add("loading");
        }
        if (sheetsRefreshIcon) {
            sheetsRefreshIcon.textContent = "\u23F3";
        }
        if (sheetsRefreshText) {
            sheetsRefreshText.textContent = "Refreshing...";
        }

        try {
            const res = await ApiClient.get("/api/admin/sheets-sync/status");
            if (res.success && res.data && res.data.success !== false) {
                const data = res.data;

                // 1. Sync Configuration
                if (sheetsConfigSyncStatus) {
                    sheetsConfigSyncStatus.innerHTML = renderSheetsBadge(
                        data.sync_enabled,
                        "Enabled",
                        "Disabled",
                        "badge-success",
                        "badge-secondary"
                    );
                }

                // 2. Credentials Configuration
                if (sheetsConfigCredsStatus) {
                    sheetsConfigCredsStatus.innerHTML = renderSheetsBadge(
                        data.credentials_configured,
                        "\u2714 Configured",
                        "\u2716 Not Configured",
                        "badge-success",
                        "badge-danger"
                    );
                }

                // 3. Spreadsheet Configuration
                if (sheetsConfigSheetStatus) {
                    sheetsConfigSheetStatus.innerHTML = renderSheetsBadge(
                        data.spreadsheet_id_configured,
                        "\u2714 Configured",
                        "\u2716 Not Configured",
                        "badge-success",
                        "badge-danger"
                    );
                }

                // 4. Last Synchronization Timestamp
                if (sheetsLastSyncTime) {
                    if (data.last_sync_timestamp) {
                        if (window.AppUtils && typeof window.AppUtils.formatDateTime === "function") {
                            sheetsLastSyncTime.textContent = window.AppUtils.formatDateTime(data.last_sync_timestamp);
                        } else {
                            try {
                                const d = new Date(data.last_sync_timestamp);
                                sheetsLastSyncTime.textContent = isNaN(d.getTime()) ? data.last_sync_timestamp : d.toLocaleString();
                            } catch {
                                sheetsLastSyncTime.textContent = data.last_sync_timestamp;
                            }
                        }
                    } else {
                        sheetsLastSyncTime.textContent = "No successful synchronization recorded yet.";
                    }
                }

                // 5. Outbox Queue Telemetry Metrics
                const qs = data.queue_stats || {};
                const formatMetric = (val) => {
                    if (typeof val === "number") return val.toLocaleString();
                    if (val !== null && val !== undefined && val !== "") return String(val);
                    return "--";
                };

                if (sheetsMetricPending) sheetsMetricPending.textContent = formatMetric(qs.pending);
                if (sheetsMetricSynced) sheetsMetricSynced.textContent = formatMetric(qs.synced);
                if (sheetsMetricInflight) sheetsMetricInflight.textContent = formatMetric(qs.in_progress !== undefined ? qs.in_progress : qs.in_flight);
                if (sheetsMetricDeadletter) sheetsMetricDeadletter.textContent = formatMetric(qs.dead_letter);
                if (sheetsMetricFailed) sheetsMetricFailed.textContent = formatMetric(qs.failed !== undefined ? qs.failed : qs.permanent_failure);
                if (sheetsMetricTotal) sheetsMetricTotal.textContent = formatMetric(qs.total);

                // Hide error banner on successful fetch
                if (sheetsSyncErrorBanner) {
                    sheetsSyncErrorBanner.style.display = "none";
                }

                if (showFeedback && typeof window.showToast === "function") {
                    window.showToast("Google Sheets status updated successfully.", "success", 2500);
                }
            } else {
                const errMsg = (res.data && (res.data.message || res.data.error)) || res.message || "Failed to retrieve Google Sheets synchronization status.";
                if (sheetsSyncErrorBanner && sheetsSyncErrorText) {
                    sheetsSyncErrorText.textContent = errMsg;
                    sheetsSyncErrorBanner.style.display = "flex";
                }
                if (sheetsConfigSyncStatus) sheetsConfigSyncStatus.innerHTML = '<span class="badge badge-warning">Unavailable</span>';
                if (sheetsConfigCredsStatus) sheetsConfigCredsStatus.innerHTML = '<span class="badge badge-warning">Unavailable</span>';
                if (sheetsConfigSheetStatus) sheetsConfigSheetStatus.innerHTML = '<span class="badge badge-warning">Unavailable</span>';
                if (sheetsLastSyncTime) sheetsLastSyncTime.textContent = "--";
                if (sheetsMetricPending) sheetsMetricPending.textContent = "--";
                if (sheetsMetricSynced) sheetsMetricSynced.textContent = "--";
                if (sheetsMetricInflight) sheetsMetricInflight.textContent = "--";
                if (sheetsMetricDeadletter) sheetsMetricDeadletter.textContent = "--";
                if (sheetsMetricFailed) sheetsMetricFailed.textContent = "--";
                if (sheetsMetricTotal) sheetsMetricTotal.textContent = "--";

                if (showFeedback && typeof window.showToast === "function") {
                    window.showToast(errMsg, "error");
                }
            }
        } catch (err) {
            const errMsg = `Error connecting to sheets status API: ${err.message}`;
            if (sheetsSyncErrorBanner && sheetsSyncErrorText) {
                sheetsSyncErrorText.textContent = errMsg;
                sheetsSyncErrorBanner.style.display = "flex";
            }
            if (sheetsConfigSyncStatus) sheetsConfigSyncStatus.innerHTML = '<span class="badge badge-warning">Unavailable</span>';
            if (sheetsConfigCredsStatus) sheetsConfigCredsStatus.innerHTML = '<span class="badge badge-warning">Unavailable</span>';
            if (sheetsConfigSheetStatus) sheetsConfigSheetStatus.innerHTML = '<span class="badge badge-warning">Unavailable</span>';
            if (sheetsLastSyncTime) sheetsLastSyncTime.textContent = "--";
            if (sheetsMetricPending) sheetsMetricPending.textContent = "--";
            if (sheetsMetricSynced) sheetsMetricSynced.textContent = "--";
            if (sheetsMetricInflight) sheetsMetricInflight.textContent = "--";
            if (sheetsMetricDeadletter) sheetsMetricDeadletter.textContent = "--";
            if (sheetsMetricFailed) sheetsMetricFailed.textContent = "--";
            if (sheetsMetricTotal) sheetsMetricTotal.textContent = "--";

            if (showFeedback && typeof window.showToast === "function") {
                window.showToast(errMsg, "error");
            }
        } finally {
            if (btnRefreshSheetsSync) {
                btnRefreshSheetsSync.disabled = false;
                btnRefreshSheetsSync.classList.remove("loading");
            }
            if (sheetsRefreshIcon) {
                sheetsRefreshIcon.textContent = "\u21BB";
            }
            if (sheetsRefreshText) {
                sheetsRefreshText.textContent = "Refresh Status";
            }
            isFetchingSheets = false;
        }
    }

    /**
     * Opens the Clear Data confirmation modal dialog.
     */
    function openClearModal() {
        if (!modalBackdrop) return;
        resetModalState();
        modalBackdrop.style.display = "flex";
        modalBackdrop.setAttribute("aria-hidden", "false");
        if (confirmInput) {
            confirmInput.focus();
        }
    }

    /**
     * Closes the Clear Data confirmation modal dialog.
     */
    function closeClearModal() {
        if (!modalBackdrop) return;
        modalBackdrop.style.display = "none";
        modalBackdrop.setAttribute("aria-hidden", "true");
        resetModalState();
    }

    /**
     * Resets input fields, error banners, and buttons in the modal.
     */
    function resetModalState() {
        if (confirmInput) {
            confirmInput.value = "";
        }
        if (btnConfirmClear) {
            btnConfirmClear.disabled = true;
            if (confirmBtnText) confirmBtnText.textContent = "Permanently Clear Data";
            if (confirmBtnIcon) confirmBtnIcon.textContent = "\u{1F5D1}\u{FE0F}";
        }
        if (modalErrorBanner) {
            modalErrorBanner.style.display = "none";
        }
        if (modalErrorText) {
            modalErrorText.textContent = "";
        }
    }

    /**
     * Validates typed confirmation phrase to enable/disable confirm button.
     */
    function handlePhraseInput() {
        if (!confirmInput || !btnConfirmClear) return;
        const typed = confirmInput.value.trim();
        const isValid = (typed === REQUIRED_CONFIRM_PHRASE);
        btnConfirmClear.disabled = !isValid;

        const hint = document.getElementById("phrase-validation-hint");
        if (hint) {
            if (isValid) {
                hint.textContent = "\u2714 Phrase matches. Click button below to proceed to final confirmation.";
                hint.style.color = "var(--color-success)";
            } else {
                hint.textContent = "Type the exact phrase above to enable the confirmation button.";
                hint.style.color = "var(--color-text-muted)";
            }
        }
    }

    /**
     * Handles the final data clearing submission after double confirmation.
     */
    async function handleClearDataSubmit() {
        if (!confirmInput || confirmInput.value.trim() !== REQUIRED_CONFIRM_PHRASE) {
            return;
        }

        // Secondary Explicit Confirmation
        const confirmed = window.confirm(
            "ARE YOU ABSOLUTELY SURE?\n\n" +
            "This action cannot be undone.\n" +
            "- All student registrations will be deleted.\n" +
            "- All class sessions will be deleted.\n" +
            "- All attendance logs will be deleted.\n\n" +
            "An automated backup will be verified and stored in the backups/ folder first.\n\n" +
            "Click OK to permanently clear operational data."
        );

        if (!confirmed) {
            return;
        }

        // Set loading state on button
        btnConfirmClear.disabled = true;
        if (confirmBtnText) confirmBtnText.textContent = "Creating Backup & Clearing...";
        if (confirmBtnIcon) confirmBtnIcon.textContent = "\u23F3";
        if (modalErrorBanner) modalErrorBanner.style.display = "none";

        try {
            const res = await ApiClient.post("/api/admin/clear-data", {
                confirmation_phrase: REQUIRED_CONFIRM_PHRASE,
            });

            if (res.success && res.data && res.data.success) {
                // Success!
                const backupFile = res.data.backup ? res.data.backup.filename : "Verified Backup";
                closeClearModal();

                if (typeof window.showToast === "function") {
                    window.showToast(
                        `Operational data cleared successfully! Backup verified: ${backupFile}`,
                        "success",
                        6000
                    );
                }

                // Dynamically refresh database statistics (will show 0s)
                await fetchDatabaseStats(false);

                // Update sidebar session status or active pills if present
                if (typeof window.pollActiveSession === "function") {
                    window.pollActiveSession();
                }

            } else {
                // Error from server
                const errMsg = (res.data && res.data.message) || res.message || "Failed to clear operational data.";
                if (modalErrorBanner && modalErrorText) {
                    modalErrorText.textContent = errMsg;
                    modalErrorBanner.style.display = "flex";
                }
                if (typeof window.showToast === "function") {
                    window.showToast(`Error: ${errMsg}`, "error", 6000);
                }
                btnConfirmClear.disabled = false;
                if (confirmBtnText) confirmBtnText.textContent = "Retry Deletion";
                if (confirmBtnIcon) confirmBtnIcon.textContent = "\u{1F5D1}\u{FE0F}";
            }
        } catch (err) {
            const errMsg = `Unexpected error: ${err.message}`;
            if (modalErrorBanner && modalErrorText) {
                modalErrorText.textContent = errMsg;
                modalErrorBanner.style.display = "flex";
            }
            if (typeof window.showToast === "function") {
                window.showToast(errMsg, "error", 6000);
            }
            btnConfirmClear.disabled = false;
            if (confirmBtnText) confirmBtnText.textContent = "Retry Deletion";
            if (confirmBtnIcon) confirmBtnIcon.textContent = "\u{1F5D1}\u{FE0F}";
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();

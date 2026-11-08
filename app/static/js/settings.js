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

        // Event Listeners
        if (btnRefreshStats) {
            btnRefreshStats.addEventListener("click", () => {
                fetchDatabaseStats(true);
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

        // Initial fetch of database stats
        fetchDatabaseStats(false);
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

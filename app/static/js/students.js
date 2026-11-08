/**
 * Smart Attendance Management System - Student Management Client Controller (students.js)
 * Handles student registration, client-side validation, duplicate handling, roster retrieval,
 * search filtering, and active/inactive status display.
 */

(function () {
    "use strict";

    // ==========================================================================
    // 1. STATE & UTILITIES
    // ==========================================================================
    let allStudents = [];
    let currentQuery = "";
    let currentFilter = "all";
    let isFetching = false;
    let isSubmitting = false;

    // Defensive active-status normalization (handles JSON booleans and SQLite integers)
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

    // Display inline form feedback (error or success banner)
    function setFormAlert(message, type = "error") {
        const alertEl = document.getElementById("form-alert");
        const iconEl = document.getElementById("form-alert-icon");
        const textEl = document.getElementById("form-alert-text");
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

    // ==========================================================================
    // 2. ROSTER DATA RETRIEVAL (GET /api/students)
    // ==========================================================================
    async function fetchStudents(silent = false) {
        if (isFetching) return;
        isFetching = true;

        const statTotal = document.getElementById("stat-total-students");
        const statActive = document.getElementById("stat-active-students");
        const statInactive = document.getElementById("stat-inactive-students");
        const rosterBadge = document.getElementById("roster-count-badge");
        const tableBody = document.getElementById("student-table-body");
        const tableErrorState = document.getElementById("table-error-state");
        const tableEmptyState = document.getElementById("table-empty-state");
        const searchEmptyState = document.getElementById("search-empty-state");
        const studentTable = document.getElementById("student-table");

        if (statTotal) statTotal.classList.add("skeleton");
        if (statActive) statActive.classList.add("skeleton");
        if (statInactive) statInactive.classList.add("skeleton");

        try {
            const res = await window.ApiClient.get("/api/students");

            if (res.success && res.data) {
                allStudents = Array.isArray(res.data.students) ? res.data.students : [];
                const totalCount = typeof res.data.count === "number" ? res.data.count : allStudents.length;
                const activeCount = allStudents.filter(isActiveStudent).length;
                const inactiveCount = totalCount - activeCount;

                if (statTotal) statTotal.textContent = totalCount;
                if (statActive) statActive.textContent = activeCount;
                if (statInactive) statInactive.textContent = inactiveCount;

                if (tableErrorState) tableErrorState.style.display = "none";
                renderTable();

                if (!silent && window.showToast && allStudents.length > 0) {
                    window.showToast("Student roster updated.", "success", 2000);
                }
            } else {
                if (statTotal) statTotal.textContent = "--";
                if (statActive) statActive.textContent = "--";
                if (statInactive) statInactive.textContent = "--";
                if (rosterBadge) rosterBadge.textContent = "Error";

                if (studentTable) studentTable.style.display = "none";
                if (tableEmptyState) tableEmptyState.style.display = "none";
                if (searchEmptyState) searchEmptyState.style.display = "none";
                if (tableErrorState) tableErrorState.style.display = "flex";
            }
        } catch {
            if (statTotal) statTotal.textContent = "--";
            if (statActive) statActive.textContent = "--";
            if (statInactive) statInactive.textContent = "--";
            if (rosterBadge) rosterBadge.textContent = "Error";

            if (studentTable) studentTable.style.display = "none";
            if (tableEmptyState) tableEmptyState.style.display = "none";
            if (searchEmptyState) searchEmptyState.style.display = "none";
            if (tableErrorState) tableErrorState.style.display = "flex";
        } finally {
            if (statTotal) statTotal.classList.remove("skeleton");
            if (statActive) statActive.classList.remove("skeleton");
            if (statInactive) statInactive.classList.remove("skeleton");
            isFetching = false;
        }
    }

    // ==========================================================================
    // 3. TABLE RENDERING, SEARCH & FILTERING
    // ==========================================================================
    function renderTable() {
        const table = document.getElementById("student-table");
        const tbody = document.getElementById("student-table-body");
        const emptyState = document.getElementById("table-empty-state");
        const searchEmptyState = document.getElementById("search-empty-state");
        const errorState = document.getElementById("table-error-state");
        const badge = document.getElementById("roster-count-badge");
        if (!tbody || !table) return;

        if (errorState) errorState.style.display = "none";

        // If entire database has no students
        if (allStudents.length === 0) {
            table.style.display = "none";
            if (emptyState) emptyState.style.display = "flex";
            if (searchEmptyState) searchEmptyState.style.display = "none";
            if (badge) badge.textContent = "0 Students";
            return;
        }

        // Apply Status Filter and Search Query concurrently
        const filtered = allStudents.filter((student) => {
            const active = isActiveStudent(student);

            // 1. Status Filter
            if (currentFilter === "active" && !active) return false;
            if (currentFilter === "inactive" && active) return false;

            // 2. Search Query (Roll Number, Full Name, Card UID)
            if (currentQuery) {
                const q = currentQuery.toLowerCase();
                const roll = (student.roll_number || "").toLowerCase();
                const name = (student.full_name || "").toLowerCase();
                const uid = (student.card_uid || "").toLowerCase();

                if (!roll.includes(q) && !name.includes(q) && !uid.includes(q)) {
                    return false;
                }
            }

            return true;
        });

        // Update Roster Count Badge
        if (badge) {
            if (currentQuery || currentFilter !== "all") {
                badge.textContent = `Showing ${filtered.length} of ${allStudents.length}`;
            } else {
                badge.textContent = `${allStudents.length} Student${allStudents.length === 1 ? "" : "s"}`;
            }
        }

        // If filter/search produces 0 results
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
                const active = isActiveStudent(s);
                const safeId = escapeHtml(s.id);
                const safeRoll = escapeHtml(s.roll_number);
                const safeName = escapeHtml(s.full_name);
                const safeUid = escapeHtml(s.card_uid);
                const formattedDate = window.AppUtils
                    ? window.AppUtils.formatDateTime(s.created_at)
                    : s.created_at || "--";

                const statusBadge = active
                    ? `<span class="badge badge-success">&#x25CF; Active</span>`
                    : `<span class="badge badge-neutral">&#x25CB; Inactive</span>`;

                return `
                    <tr id="student-row-${safeId}">
                        <td>${safeId}</td>
                        <td class="cell-roll">${safeRoll}</td>
                        <td class="cell-name">${safeName}</td>
                        <td><code class="code-inline cell-uid">${safeUid}</code></td>
                        <td>${statusBadge}</td>
                        <td class="cell-date">${formattedDate}</td>
                    </tr>
                `;
            })
            .join("");
    }

    // ==========================================================================
    // 4. STUDENT REGISTRATION FORM SUBMISSION (POST /api/students)
    // ==========================================================================
    async function handleFormSubmit(event) {
        event.preventDefault();
        clearFormAlert();

        if (isSubmitting) return;

        const rollInput = document.getElementById("student-roll-number");
        const nameInput = document.getElementById("student-full-name");
        const uidInput = document.getElementById("student-card-uid");
        const btnSubmit = document.getElementById("btn-submit-student");
        const form = document.getElementById("form-register-student");

        if (!rollInput || !nameInput || !uidInput) return;

        const rollNumber = rollInput.value.trim();
        const fullName = nameInput.value.trim();
        const cardUid = uidInput.value.trim().toUpperCase();

        // 1. Client-Side Validation
        if (!rollNumber) {
            setFormAlert("Please provide a valid Roll Number.", "error");
            rollInput.focus();
            return;
        }
        if (rollNumber.length > 50) {
            setFormAlert("Roll Number cannot exceed 50 characters.", "error");
            rollInput.focus();
            return;
        }

        if (!fullName) {
            setFormAlert("Please provide the student's Full Name.", "error");
            nameInput.focus();
            return;
        }
        if (fullName.length < 2 || fullName.length > 100) {
            setFormAlert("Full Name must be between 2 and 100 characters.", "error");
            nameInput.focus();
            return;
        }

        if (!cardUid) {
            setFormAlert("Please provide an RFID Card UID.", "error");
            uidInput.focus();
            return;
        }
        if (cardUid.length < 4 || cardUid.length > 50) {
            setFormAlert("RFID Card UID must be between 4 and 50 characters.", "error");
            uidInput.focus();
            return;
        }
        if (!/^[A-Z0-9_-]+$/.test(cardUid)) {
            setFormAlert("RFID Card UID must contain only alphanumeric characters, dashes, or underscores.", "error");
            uidInput.focus();
            return;
        }

        // 2. Set Submitting Loading State
        isSubmitting = true;
        if (btnSubmit) {
            btnSubmit.disabled = true;
            btnSubmit.innerHTML = `<span class="btn-icon">&#x21BB;</span> Registering...`;
        }

        try {
            const payload = {
                roll_number: rollNumber,
                full_name: fullName,
                card_uid: cardUid,
            };

            const res = await window.ApiClient.post("/api/students", payload);

            if (res.success && res.status === 201) {
                // Success: clear form, display success message and toast, refresh roster
                setFormAlert(`Student "${fullName}" (Roll: ${rollNumber}) registered successfully!`, "success");
                if (window.showToast) {
                    window.showToast(`Student ${fullName} registered.`, "success", 3000);
                }

                form.reset();
                await fetchStudents(true);

                // Highlight newly registered student row if visible
                const newId = res.data && res.data.student ? res.data.student.id : null;
                if (newId) {
                    const row = document.getElementById(`student-row-${newId}`);
                    if (row) {
                        row.style.backgroundColor = "#ecfdf5";
                        setTimeout(() => {
                            row.style.backgroundColor = "";
                        }, 2500);
                    }
                }
            } else {
                // Failure: preserve user input so they can easily fix it
                let errMsg = res.message || "Failed to register student.";
                if (res.status === 409) {
                    errMsg = `Conflict: ${errMsg}`;
                } else if (res.status === 400) {
                    errMsg = `Validation Error: ${errMsg}`;
                }

                setFormAlert(errMsg, "error");
                if (window.showToast) {
                    window.showToast(errMsg, "error", 4000);
                }
            }
        } catch (err) {
            setFormAlert(`Connection Error: ${err.message}`, "error");
            if (window.showToast) {
                window.showToast("Connection error while registering student.", "error", 4000);
            }
        } finally {
            isSubmitting = false;
            if (btnSubmit) {
                btnSubmit.disabled = false;
                btnSubmit.innerHTML = `<span class="btn-icon">&#x2795;</span> Register Student`;
            }
        }
    }

    // ==========================================================================
    // 5. EVENT LISTENERS & INITIALIZATION
    // ==========================================================================
    function initStudentEvents() {
        // Form submission
        const form = document.getElementById("form-register-student");
        if (form) {
            form.addEventListener("submit", handleFormSubmit);
        }

        // Form reset button
        const btnReset = document.getElementById("btn-reset-form");
        if (btnReset && form) {
            btnReset.addEventListener("click", () => {
                form.reset();
                clearFormAlert();
            });
        }

        // Auto-uppercase RFID Card UID input as user types
        const uidInput = document.getElementById("student-card-uid");
        if (uidInput) {
            uidInput.addEventListener("input", () => {
                const start = uidInput.selectionStart;
                const end = uidInput.selectionEnd;
                uidInput.value = uidInput.value.toUpperCase();
                uidInput.setSelectionRange(start, end);
            });
        }

        // Search Input
        const searchInput = document.getElementById("search-students");
        const btnClearSearch = document.getElementById("btn-clear-search");

        if (searchInput) {
            searchInput.addEventListener("input", (e) => {
                currentQuery = e.target.value.trim();
                if (btnClearSearch) {
                    btnClearSearch.style.display = currentQuery ? "inline-block" : "none";
                }
                renderTable();
            });
        }

        if (btnClearSearch && searchInput) {
            btnClearSearch.addEventListener("click", () => {
                searchInput.value = "";
                currentQuery = "";
                btnClearSearch.style.display = "none";
                searchInput.focus();
                renderTable();
            });
        }

        // Filter Select
        const filterSelect = document.getElementById("filter-status");
        if (filterSelect) {
            filterSelect.addEventListener("change", (e) => {
                currentFilter = e.target.value;
                renderTable();
            });
        }

        // Refresh Button
        const btnRefresh = document.getElementById("btn-refresh-students");
        if (btnRefresh) {
            btnRefresh.addEventListener("click", () => fetchStudents(false));
        }

        // Scroll to Register Button
        const btnScroll = document.getElementById("btn-scroll-to-register");
        const rollInput = document.getElementById("student-roll-number");
        if (btnScroll && rollInput) {
            btnScroll.addEventListener("click", () => {
                rollInput.scrollIntoView({ behavior: "smooth", block: "center" });
                rollInput.focus();
            });
        }
    }

    // Expose for debugging and tests
    window.fetchStudents = fetchStudents;
    window.renderStudentTable = renderTable;

    document.addEventListener("DOMContentLoaded", () => {
        initStudentEvents();
        fetchStudents(true);
    });
})();

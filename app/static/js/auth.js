/**
 * Smart Attendance Management System - Authentication Controller (auth.js)
 * Manages administrator login form submission, show/hide password toggle,
 * empty-field validation, loading state, and error alerts.
 */

(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("login-form");
        const usernameInput = document.getElementById("username");
        const passwordInput = document.getElementById("password");
        const togglePasswordBtn = document.getElementById("btn-toggle-password");
        const eyeIcon = document.getElementById("eye-icon");
        const submitBtn = document.getElementById("btn-login");
        const submitSpinner = document.getElementById("btn-login-spinner");
        const submitText = document.getElementById("btn-login-text");
        const alertBox = document.getElementById("auth-alert");
        const alertText = document.getElementById("auth-alert-text");
        const usernameError = document.getElementById("username-error");
        const passwordError = document.getElementById("password-error");
        const csrfInput = document.getElementById("csrf-token-input");
        const nextInput = document.getElementById("next-url-input");

        // 1. Show / Hide Password Toggle
        if (togglePasswordBtn && passwordInput) {
            togglePasswordBtn.addEventListener("click", () => {
                const isPassword = passwordInput.type === "password";
                passwordInput.type = isPassword ? "text" : "password";
                if (eyeIcon) {
                    eyeIcon.innerHTML = isPassword ? "&#x1F648;" : "&#x1F441;";
                }
                togglePasswordBtn.setAttribute(
                    "aria-label",
                    isPassword ? "Hide password" : "Show password"
                );
            });
        }

        // Helper: Show Error Alert
        function showError(message) {
            if (alertText) alertText.textContent = message;
            if (alertBox) {
                alertBox.style.display = "flex";
                alertBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }
        }

        // Helper: Hide Error Alert
        function hideError() {
            if (alertBox) alertBox.style.display = "none";
            if (usernameError) usernameError.textContent = "";
            if (passwordError) passwordError.textContent = "";
            if (usernameInput) usernameInput.classList.remove("input-error");
            if (passwordInput) passwordInput.classList.remove("input-error");
        }

        // Helper: Set Button Loading State
        function setLoading(isLoading) {
            if (submitBtn) submitBtn.disabled = isLoading;
            if (submitSpinner) submitSpinner.style.display = isLoading ? "inline-block" : "none";
            if (submitText) submitText.textContent = isLoading ? "Signing In..." : "Sign In";
        }

        // Clear error highlights on user input
        if (usernameInput) {
            usernameInput.addEventListener("input", () => {
                usernameInput.classList.remove("input-error");
                if (usernameError) usernameError.textContent = "";
            });
        }
        if (passwordInput) {
            passwordInput.addEventListener("input", () => {
                passwordInput.classList.remove("input-error");
                if (passwordError) passwordError.textContent = "";
            });
        }

        // 2. Form Submission with Validation and API Integration
        if (form) {
            form.addEventListener("submit", async (e) => {
                e.preventDefault();
                hideError();

                const username = usernameInput ? usernameInput.value.trim() : "";
                const password = passwordInput ? passwordInput.value : "";
                const csrfToken = csrfInput ? csrfInput.value : "";
                const nextUrl = nextInput ? nextInput.value : "";

                let hasError = false;

                // Client-side validation: Empty username
                if (!username) {
                    if (usernameInput) usernameInput.classList.add("input-error");
                    if (usernameError) usernameError.textContent = "Please enter your username.";
                    hasError = true;
                }

                // Client-side validation: Empty password
                if (!password) {
                    if (passwordInput) passwordInput.classList.add("input-error");
                    if (passwordError) passwordError.textContent = "Please enter your password.";
                    hasError = true;
                }

                if (hasError) {
                    showError("Please fill in all required fields.");
                    return;
                }

                // Proceed with login request
                setLoading(true);

                try {
                    const response = await fetch("/login", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "X-CSRF-Token": csrfToken,
                        },
                        body: JSON.stringify({
                            username: username,
                            password: password,
                            csrf_token: csrfToken,
                            next: nextUrl,
                        }),
                    });

                    let data = null;
                    try {
                        data = await response.json();
                    } catch {
                        data = null;
                    }

                    if (response.ok && data && data.success) {
                        // Successful authentication
                        const redirectUrl = data.redirect || "/dashboard";
                        window.location.href = redirectUrl;
                    } else {
                        // Failed authentication (401, 400, 429, 500)
                        setLoading(false);
                        const msg = (data && data.message) || "Invalid username or password.";
                        showError(msg);
                        if (passwordInput) {
                            passwordInput.value = "";
                            passwordInput.focus();
                        }
                    }
                } catch (err) {
                    setLoading(false);
                    console.error("Login network failure:", err);
                    showError("Unable to connect to the authentication service. Please check your connection.");
                }
            });
        }
    });
})();

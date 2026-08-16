const API_BASE = "http://127.0.0.1:8000/api/v1";

let accessToken = localStorage.getItem("photoflow_admin_access_token");


// =========================================================
// API
// =========================================================

async function apiRequest(path, options = {}) {
    const headers = {
        "Content-Type": "application/json",
        ...(options.headers || {})
    };

    if (accessToken) {
        headers.Authorization = `Bearer ${accessToken}`;
    }

    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers
    });

    let data = null;

    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (response.status === 401) {
        accessToken = null;
        localStorage.removeItem("photoflow_admin_access_token");
        localStorage.removeItem("photoflow_admin_refresh_token");
        localStorage.removeItem("photoflow_admin_user");

        showLogin();
        throw new Error("Your session has expired. Please sign in again.");
    }

    if (!response.ok) {
        throw new Error(
            data?.detail || `Request failed (${response.status})`
        );
    }

    return data;
}


// =========================================================
// AUTH
// =========================================================

async function login(email, password) {
    const response = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            email,
            password
        })
    });

    let data = null;

    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (!response.ok) {
        throw new Error(data?.detail || "Invalid credentials.");
    }

    if (!data.user || data.user.role !== "ADMIN") {
        throw new Error(
            "This account does not have administrator access."
        );
    }

    accessToken = data.access_token;

    localStorage.setItem(
        "photoflow_admin_access_token",
        data.access_token
    );

    localStorage.setItem(
        "photoflow_admin_refresh_token",
        data.refresh_token
    );

    localStorage.setItem(
        "photoflow_admin_user",
        JSON.stringify(data.user)
    );

    return data;
}


async function getCurrentAdmin() {
    return await apiRequest("/auth/me");
}


async function logout() {
    const refreshToken = localStorage.getItem(
        "photoflow_admin_refresh_token"
    );

    try {
        if (refreshToken) {
            await apiRequest("/auth/logout", {
                method: "POST",
                body: JSON.stringify({
                    refresh_token: refreshToken
                })
            });
        }
    } catch {
        // Always clear local credentials.
    }

    accessToken = null;

    localStorage.removeItem("photoflow_admin_access_token");
    localStorage.removeItem("photoflow_admin_refresh_token");
    localStorage.removeItem("photoflow_admin_user");

    showLogin();
}


// =========================================================
// USERS
// =========================================================

async function getUsers(options = {}) {
    const params = new URLSearchParams();

    params.set("limit", options.limit || 50);
    params.set("offset", options.offset || 0);

    if (options.role) {
        params.set("role", options.role);
    }

    if (options.status) {
        params.set("status", options.status);
    }

    return await apiRequest(
        `/admin/users?${params.toString()}`
    );
}


async function createUser(payload) {
    return await apiRequest("/admin/users", {
        method: "POST",
        body: JSON.stringify(payload)
    });
}


async function getUser(userId) {
    return await apiRequest(`/admin/users/${userId}`);
}


async function disableUser(userId) {
    return await apiRequest(
        `/admin/users/${userId}/disable`,
        {
            method: "POST"
        }
    );
}


async function enableUser(userId) {
    return await apiRequest(
        `/admin/users/${userId}/enable`,
        {
            method: "POST"
        }
    );
}


// =========================================================
// LICENSES
// =========================================================

async function getLicenses(options = {}) {
    const params = new URLSearchParams();

    params.set("limit", options.limit || 50);
    params.set("offset", options.offset || 0);

    if (options.status) {
        params.set("status", options.status);
    }

    if (options.userId) {
        params.set("user_id", options.userId);
    }

    return await apiRequest(
        `/admin/licenses?${params.toString()}`
    );
}


async function getLicense(licenseId) {
    return await apiRequest(
        `/admin/licenses/${licenseId}`
    );
}


async function createLicense(payload) {
    return await apiRequest("/admin/licenses", {
        method: "POST",
        body: JSON.stringify(payload)
    });
}


async function suspendLicense(licenseId) {
    return await apiRequest(
        `/admin/licenses/${licenseId}/suspend`,
        {
            method: "POST"
        }
    );
}


async function revokeLicense(licenseId) {
    return await apiRequest(
        `/admin/licenses/${licenseId}/revoke`,
        {
            method: "POST"
        }
    );
}


async function getLicenseDevices(licenseId) {
    return await apiRequest(
        `/admin/licenses/${licenseId}/devices`
    );
}


async function deactivateDevice(licenseId, deviceId) {
    return await apiRequest(
        `/admin/licenses/${licenseId}/devices/${deviceId}/deactivate`,
        {
            method: "POST"
        }
    );
}


// =========================================================
// RELEASES
// =========================================================

async function getReleases(options = {}) {
    const params = new URLSearchParams();

    params.set("limit", options.limit || 50);
    params.set("offset", options.offset || 0);

    if (options.status) {
        params.set("status", options.status);
    }

    return await apiRequest(
        `/admin/releases?${params.toString()}`
    );
}


async function createRelease(payload) {
    return await apiRequest("/admin/releases", {
        method: "POST",
        body: JSON.stringify(payload)
    });
}


async function publishRelease(releaseId) {
    return await apiRequest(
        `/admin/releases/${releaseId}/publish`,
        {
            method: "POST"
        }
    );
}


async function yankRelease(releaseId) {
    return await apiRequest(
        `/admin/releases/${releaseId}/yank`,
        {
            method: "POST"
        }
    );
}


// =========================================================
// UI
// =========================================================

function showLogin() {
    const loginScreen = document.getElementById("login-screen");
    const dashboard = document.getElementById("dashboard");

    if (loginScreen) {
        loginScreen.style.display = "";
    }

    if (dashboard) {
        dashboard.style.display = "none";
    }
}


function showDashboard() {
    const loginScreen = document.getElementById("login-screen");
    const dashboard = document.getElementById("dashboard");

    if (loginScreen) {
        loginScreen.style.display = "none";
    }

    if (dashboard) {
        dashboard.style.display = "";
    }
}


function showError(message) {
    console.error(message);

    const element = document.getElementById("error-message");

    if (element) {
        element.textContent = message;
        element.style.display = "";
    } else {
        alert(message);
    }
}


function clearError() {
    const element = document.getElementById("error-message");

    if (element) {
        element.textContent = "";
        element.style.display = "none";
    }
}


function showSuccess(message) {
    const element = document.getElementById("success-message");

    if (element) {
        element.textContent = message;
        element.style.display = "";
        setTimeout(() => {
            element.style.display = "none";
        }, 4000);
    } else {
        alert(message);
    }
}


function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function formatDate(value) {
    if (!value) {
        return "—";
    }

    try {
        return new Date(value).toLocaleString();
    } catch {
        return value;
    }
}


function statusClass(status) {
    return String(status || "")
        .toLowerCase()
        .replaceAll("_", "-");
}


function releaseStatusBadgeClass(status) {
    /*
     * Reuses the existing badge palette rather than adding new CSS:
     * DRAFT reads as "not live yet" (amber, same as pending/suspended),
     * PUBLISHED as live (green, same as active), YANKED as removed (red,
     * same as revoked/disabled).
     */
    switch (status) {
        case "PUBLISHED":
            return "badge-active";
        case "YANKED":
            return "badge-revoked";
        default:
            return "badge-pending";
    }
}


function formatBytes(bytes) {
    if (!bytes && bytes !== 0) {
        return "—";
    }

    return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
}


// =========================================================
// DASHBOARD INITIALIZATION
// =========================================================

async function initializeDashboard() {
    try {
        const user = await getCurrentAdmin();

        if (user.role !== "ADMIN") {
            throw new Error("Administrator access required.");
        }

        const adminName = document.getElementById("admin-name");

        if (adminName) {
            adminName.textContent =
                user.name || user.email;
        }

        await loadDashboardData();

    } catch (error) {
        console.error(error);

        accessToken = null;

        localStorage.removeItem(
            "photoflow_admin_access_token"
        );

        showLogin();
        showError(error.message);
    }
}


async function loadDashboardData() {
    clearError();

    try {
        const [users, licenses] = await Promise.all([
            getUsers({ limit: 200 }),
            getLicenses({ limit: 200 })
        ]);

        renderOverview(users, licenses);
        renderUsers(users);
        renderLicenses(licenses);

    } catch (error) {
        showError(error.message);
    }
}


// =========================================================
// OVERVIEW
// =========================================================

function renderOverview(usersData, licensesData) {
    const users = usersData?.items || [];
    const licenses = Array.isArray(licensesData)
        ? licensesData
        : [];

    const activeUsers = users.filter(
        user => user.status === "ACTIVE"
    ).length;

    const activeLicenses = licenses.filter(
        license => license.status === "ACTIVE"
    ).length;

    const activeDevices = licenses.reduce(
        (total, license) =>
            total + Number(license.active_devices || 0),
        0
    );

    setText("stat-users", usersData?.total ?? users.length);
    setText("stat-active-users", activeUsers);
    setText("stat-licenses", licenses.length);
    setText("stat-active-licenses", activeLicenses);
    setText("stat-devices", activeDevices);
}


// =========================================================
// USERS RENDERING
// =========================================================

function renderUsers(data) {
    const container =
        document.getElementById("users-table");

    if (!container) {
        return;
    }

    const users = data?.items || [];

    if (users.length === 0) {
        container.innerHTML =
            "<p class='empty-state'>No users found.</p>";
        return;
    }

    container.innerHTML = users.map(user => `
        <div class="table-row">
            <div>
                <strong>${escapeHtml(user.name)}</strong>
            </div>

            <div>
                ${escapeHtml(user.email)}
            </div>

            <div>
                <span class="status ${statusClass(user.role)}">
                    ${escapeHtml(user.role)}
                </span>
            </div>

            <div>
                <span class="status ${statusClass(user.status)}">
                    ${escapeHtml(user.status)}
                </span>
            </div>

            <div>
                ${user.status === "ACTIVE"
            ? `
                        <button
                            class="action-button danger"
                            onclick="handleDisableUser('${user.id}')">
                            Disable
                        </button>
                    `
            : `
                        <button
                            class="action-button"
                            onclick="handleEnableUser('${user.id}')">
                            Enable
                        </button>
                    `
        }
            </div>
        </div>
    `).join("");
}


// =========================================================
// LICENSE RENDERING
// =========================================================

function renderLicenses(data) {
    const container =
        document.getElementById("licenses-table");

    if (!container) {
        return;
    }

    /*
     * IMPORTANT:
     * /admin/licenses returns a LIST directly.
     * It does NOT return {items: [...]}
     */
    const licenses = Array.isArray(data)
        ? data
        : [];

    if (licenses.length === 0) {
        container.innerHTML =
            "<p class='empty-state'>No licenses found.</p>";
        return;
    }

    container.innerHTML = licenses.map(license => `
        <div class="table-row">
            <div>
                <strong>
                    ••••${escapeHtml(license.key_last4)}
                </strong>
            </div>

            <div>
                ${escapeHtml(license.user_name)}
                <small>
                    ${escapeHtml(license.user_email)}
                </small>
            </div>

            <div>
                ${escapeHtml(license.plan)}
            </div>

            <div>
                <span class="status ${statusClass(license.status)}">
                    ${escapeHtml(license.status)}
                </span>
            </div>

            <div>
                ${license.active_devices ?? 0}
                /
                ${license.activation_limit ?? 0}
            </div>

            <div>
                ${formatDate(license.expires_at)}
            </div>

            <div class="row-actions">
                <button
                    class="action-button"
                    onclick="handleViewDevices('${license.id}')">
                    Devices
                </button>

                ${license.status === "ACTIVE"
            ? `
                        <button
                            class="action-button warning"
                            onclick="handleSuspendLicense('${license.id}')">
                            Suspend
                        </button>

                        <button
                            class="action-button danger"
                            onclick="handleRevokeLicense('${license.id}')">
                            Revoke
                        </button>
                    `
            : ""
        }
            </div>
        </div>
    `).join("");
}


// =========================================================
// USER ACTIONS
// =========================================================

async function handleDisableUser(userId) {
    if (!confirm(
        "Disable this account? Existing sessions will be revoked."
    )) {
        return;
    }

    try {
        await disableUser(userId);

        showSuccess("Account disabled.");

        await loadDashboardData();

    } catch (error) {
        showError(error.message);
    }
}


async function handleEnableUser(userId) {
    if (!confirm("Enable this account?")) {
        return;
    }

    try {
        await enableUser(userId);

        showSuccess("Account enabled.");

        await loadDashboardData();

    } catch (error) {
        showError(error.message);
    }
}


// =========================================================
// LICENSE ACTIONS
// =========================================================

async function handleSuspendLicense(licenseId) {
    if (!confirm(
        "Suspend this license? Active devices will no longer validate it."
    )) {
        return;
    }

    try {
        await suspendLicense(licenseId);

        showSuccess("License suspended.");

        await loadDashboardData();

    } catch (error) {
        showError(error.message);
    }
}


async function handleRevokeLicense(licenseId) {
    if (!confirm(
        "Revoke this license permanently? Active device activations will be deactivated."
    )) {
        return;
    }

    try {
        await revokeLicense(licenseId);

        showSuccess("License revoked.");

        await loadDashboardData();

    } catch (error) {
        showError(error.message);
    }
}


// =========================================================
// DEVICES
// =========================================================

async function handleViewDevices(licenseId) {
    try {
        const devices =
            await getLicenseDevices(licenseId);

        renderDevices(devices, licenseId);

        showSection("devices");

    } catch (error) {
        showError(error.message);
    }
}


function renderDevices(devices, licenseId) {
    const container =
        document.getElementById("devices-table");

    if (!container) {
        return;
    }

    if (!devices || devices.length === 0) {
        container.innerHTML =
            "<p class='empty-state'>No devices found.</p>";
        return;
    }

    container.innerHTML = devices.map(device => `
        <div class="table-row">
            <div>
                <strong>
                    ${escapeHtml(device.name || "Unnamed device")}
                </strong>
            </div>

            <div>
                ${escapeHtml(device.platform || "Unknown")}
            </div>

            <div>
                ${escapeHtml(device.app_version || "—")}
            </div>

            <div>
                <span class="status ${statusClass(device.status)}">
                    ${escapeHtml(device.status)}
                </span>
            </div>

            <div>
                ${formatDate(device.last_seen_at)}
            </div>

            <div>
                ${device.status === "ACTIVE"
            ? `
                        <button
                            class="action-button danger"
                            onclick="handleDeactivateDevice(
                                '${licenseId}',
                                '${device.device_id}'
                            )">
                            Deactivate
                        </button>
                    `
            : ""
        }
            </div>
        </div>
    `).join("");
}


// =========================================================
// RELEASES RENDERING
// =========================================================

function renderReleases(releases) {
    const container =
        document.getElementById("releases-full-table");

    if (!container) {
        return;
    }

    if (!releases || releases.length === 0) {
        container.innerHTML =
            "<p class='empty-state'>No releases registered yet.</p>";
        return;
    }

    const rows = releases.map(release => `
        <tr>
            <td>
                <span class="table-primary">${escapeHtml(release.version)}</span>
                <span class="table-secondary">
                    ${escapeHtml(release.product)} · ${escapeHtml(release.platform || "—")} · ${escapeHtml(release.channel)}
                </span>
            </td>

            <td>
                <span class="badge ${releaseStatusBadgeClass(release.status)}">
                    ${escapeHtml(release.status)}
                </span>
            </td>

            <td>
                ${escapeHtml(release.installer_filename || "—")}
                <span class="table-secondary">${formatBytes(release.size_bytes)}</span>
            </td>

            <td>
                ${formatDate(release.published_at)}
            </td>

            <td class="table-actions">
                ${release.status !== "PUBLISHED"
            ? `
                        <button
                            class="table-action"
                            onclick="handlePublishRelease('${release.id}')">
                            Publish
                        </button>
                    `
            : `
                        <button
                            class="table-action danger"
                            onclick="handleYankRelease('${release.id}')">
                            Yank
                        </button>
                    `
        }
            </td>
        </tr>
    `).join("");

    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Installer</th>
                    <th>Release date</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${rows}
            </tbody>
        </table>
    `;
}


async function loadReleases() {
    const container =
        document.getElementById("releases-full-table");

    try {
        const releases = await getReleases({ limit: 200 });
        renderReleases(releases);
    } catch (error) {
        if (container) {
            container.innerHTML =
                "<p class='empty-state'>Could not load releases.</p>";
        }
        showError(error.message);
    }
}


// =========================================================
// RELEASE ACTIONS
// =========================================================

async function handleCreateRelease(event) {
    event.preventDefault();

    const form = event.target;

    const sizeMb = Number(
        document.getElementById("release-size")?.value || 0
    );

    const sha256 =
        document.getElementById("release-sha256")?.value.trim() || null;

    const releaseNotes =
        document.getElementById("release-notes")?.value.trim() || null;

    const releaseNotesUrl =
        document.getElementById("release-notes-url")?.value.trim() || null;

    const minimumSupportedVersion =
        document.getElementById("release-min-version")?.value.trim() || null;

    const critical =
        document.getElementById("release-critical")?.checked || false;

    const payload = {
        version: document.getElementById("release-version")?.value.trim(),
        product: "photoflow",
        platform: document.getElementById("release-platform")?.value,
        channel: document.getElementById("release-channel")?.value,
        installer_filename:
            document.getElementById("release-filename")?.value.trim(),
        size_bytes: Math.round(sizeMb * 1024 * 1024),
        download_url: document.getElementById("release-url")?.value.trim(),
        sha256: sha256,
        release_notes: releaseNotes,
        release_notes_url: releaseNotesUrl,
        minimum_supported_version: minimumSupportedVersion,
        critical: critical
    };

    try {
        await createRelease(payload);

        closeModal("release-modal");

        form.reset();

        showSuccess("Release registered as a draft.");

        await loadReleases();

    } catch (error) {
        showError(error.message);
    }
}


async function handlePublishRelease(releaseId) {
    if (!confirm(
        "Publish this release? It becomes the download the website and desktop app offer immediately."
    )) {
        return;
    }

    try {
        await publishRelease(releaseId);

        showSuccess("Release published.");

        await loadReleases();

    } catch (error) {
        showError(error.message);
    }
}


async function handleYankRelease(releaseId) {
    if (!confirm(
        "Yank this release? It stops appearing as the current download; installs already made are unaffected."
    )) {
        return;
    }

    try {
        await yankRelease(releaseId);

        showSuccess("Release yanked.");

        await loadReleases();

    } catch (error) {
        showError(error.message);
    }
}


async function handleDeactivateDevice(
    licenseId,
    deviceId
) {
    if (!confirm(
        "Deactivate this device and release its license seat?"
    )) {
        return;
    }

    try {
        await deactivateDevice(
            licenseId,
            deviceId
        );

        showSuccess("Device deactivated.");

        await handleViewDevices(licenseId);

    } catch (error) {
        showError(error.message);
    }
}


// =========================================================
// CREATE USER
// =========================================================

async function handleCreateUser(event) {
    event.preventDefault();

    const form = event.target;

    const payload = {
        name: form.querySelector("[name='name']").value.trim(),
        email: form.querySelector("[name='email']").value.trim(),
        password: form.querySelector("[name='password']").value,
        role:
            form.querySelector("[name='role']")?.value
            || "CLIENT"
    };

    try {
        await createUser(payload);

        closeModal("create-user-modal");

        form.reset();

        showSuccess("Customer account created.");

        await loadDashboardData();

    } catch (error) {
        showError(error.message);
    }
}


// =========================================================
// CREATE LICENSE
// =========================================================

async function handleCreateLicense(event) {
    event.preventDefault();

    const form = event.target;

    const userId =
        form.querySelector("[name='user_id']").value;

    const plan =
        form.querySelector("[name='plan']").value.trim();

    const activationLimit =
        Number(
            form.querySelector("[name='activation_limit']")?.value
            || 1
        );

    const notes =
        form.querySelector("[name='notes']")?.value.trim()
        || null;

    const startsAt =
        form.querySelector("[name='starts_at']")?.value
        || null;

    const expiresAt =
        form.querySelector("[name='expires_at']")?.value
        || null;

    if (!userId || !plan) {
        showError("Customer and plan are required.");
        return;
    }

    try {
        const result = await createLicense({
            user_id: userId,
            plan,
            activation_limit: activationLimit,
            starts_at: startsAt
                ? new Date(startsAt).toISOString()
                : null,
            expires_at: expiresAt
                ? new Date(expiresAt).toISOString()
                : null,
            notes
        });

        closeModal("create-license-modal");

        form.reset();

        /*
         * The raw license key is returned ONLY at creation time.
         * Show it immediately so the administrator can copy it.
         */
        showLicenseKey(result.key);

        await loadDashboardData();

    } catch (error) {
        showError(error.message);
    }
}


// =========================================================
// LICENSE KEY DISPLAY
// =========================================================

function showLicenseKey(key) {
    const element =
        document.getElementById("generated-license-key");

    if (!element) {
        alert(`License created:\n\n${key}`);
        return;
    }

    element.textContent = key;

    const modal =
        document.getElementById("license-key-modal");

    if (modal) {
        modal.style.display = "";
    }
}


function copyGeneratedLicenseKey() {
    const element =
        document.getElementById("generated-license-key");

    if (!element) {
        return;
    }

    navigator.clipboard.writeText(
        element.textContent
    ).then(() => {
        showSuccess("License key copied.");
    }).catch(() => {
        showError(
            "Could not copy the license key."
        );
    });
}


// =========================================================
// MODALS
// =========================================================

function openModal(id) {
    const modal = document.getElementById(id);

    if (modal) {
        modal.style.display = "";
    }
}


function closeModal(id) {
    const modal = document.getElementById(id);

    if (modal) {
        modal.style.display = "none";
    }
}


// =========================================================
// NAVIGATION
// =========================================================

const SECTION_TITLES = {
    overview: "Overview",
    customers: "Customers",
    licenses: "Licenses",
    devices: "Devices",
    releases: "Releases"
};


function showSection(sectionName) {
    /*
     * One mechanism for every tab: sections are toggled with the
     * `.active-section` class admin.css already defines
     * (`.dashboard-section.active-section { display: block }`), and the
     * matching sidebar button is the one whose [data-section] equals
     * sectionName. This replaces the old showSection()/showReleasesSection()
     * split, where showSection() looked for [data-nav] (nothing in the page
     * has that attribute) while the sidebar buttons actually carry
     * [data-section], so only the Releases tab -- which had its own
     * special-cased function -- ever actually switched sections.
     */
    document
        .querySelectorAll(".dashboard-section")
        .forEach(section => {
            section.classList.toggle(
                "active-section",
                section.id === `section-${sectionName}`
            );
        });

    document
        .querySelectorAll(".sidebar .nav-item[data-section]")
        .forEach(item => {
            item.classList.toggle(
                "active",
                item.dataset.section === sectionName
            );
        });

    const pageTitle = document.getElementById("page-title");

    if (pageTitle) {
        pageTitle.textContent =
            SECTION_TITLES[sectionName] || sectionName;
    }
}


function setupNavigation() {
    document
        .querySelectorAll(".sidebar .nav-item[data-section]")
        .forEach(button => {
            button.addEventListener(
                "click",
                () => {
                    const section = button.dataset.section;

                    showSection(section);

                    if (section === "overview") {
                        loadDashboardData();
                    }

                    if (section === "customers") {
                        loadDashboardData();
                    }

                    if (section === "licenses") {
                        loadDashboardData();
                    }

                    if (section === "releases") {
                        loadReleases();
                    }
                }
            );
        });
}


// =========================================================
// HELPERS
// =========================================================

function setText(id, value) {
    const element =
        document.getElementById(id);

    if (element) {
        element.textContent = value;
    }
}


// =========================================================
// EVENT WIRING
// =========================================================

function setupLoginForm() {
    const form =
        document.getElementById("login-form");

    if (!form) {
        return;
    }

    form.addEventListener(
        "submit",
        async event => {
            event.preventDefault();

            clearError();

            const email =
                document
                    .getElementById("email")
                    ?.value
                    ?.trim();

            const password =
                document
                    .getElementById("password")
                    ?.value;

            if (!email || !password) {
                showError(
                    "Enter your email and password."
                );
                return;
            }

            const button =
                form.querySelector(
                    "button[type='submit']"
                );

            if (button) {
                button.disabled = true;
                button.textContent =
                    "Signing in...";
            }

            try {
                await login(
                    email,
                    password
                );

                showDashboard();

                await initializeDashboard();

            } catch (error) {
                showError(error.message);

            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent =
                        "Sign in";
                }
            }
        }
    );
}


function setupForms() {
    const userForm =
        document.getElementById(
            "create-user-form"
        );

    if (userForm) {
        userForm.addEventListener(
            "submit",
            handleCreateUser
        );
    }

    const licenseForm =
        document.getElementById(
            "create-license-form"
        );

    if (licenseForm) {
        licenseForm.addEventListener(
            "submit",
            handleCreateLicense
        );
    }

    const releaseForm =
        document.getElementById("release-form");

    if (releaseForm) {
        releaseForm.addEventListener(
            "submit",
            handleCreateRelease
        );
    }
}


function setupButtons() {
    const logoutButton =
        document.getElementById(
            "logout-button"
        );

    if (logoutButton) {
        logoutButton.addEventListener(
            "click",
            logout
        );
    }

    const createUserButton =
        document.getElementById(
            "create-user-button"
        );

    if (createUserButton) {
        createUserButton.addEventListener(
            "click",
            () => openModal("create-user-modal")
        );
    }

    const createLicenseButton =
        document.getElementById(
            "create-license-button"
        );

    if (createLicenseButton) {
        createLicenseButton.addEventListener(
            "click",
            async () => {
                await populateLicenseUsers();
                openModal("create-license-modal");
            }
        );
    }

    const createReleaseButton =
        document.getElementById("create-release-button");

    if (createReleaseButton) {
        createReleaseButton.addEventListener(
            "click",
            () => openModal("release-modal")
        );
    }

    const copyButton =
        document.getElementById(
            "copy-license-key"
        );

    if (copyButton) {
        copyButton.addEventListener(
            "click",
            copyGeneratedLicenseKey
        );
    }

    document
        .querySelectorAll("[data-close-modal]")
        .forEach(button => {
            button.addEventListener(
                "click",
                () => {
                    closeModal(
                        button.dataset.closeModal
                    );
                }
            );
        });
}


// =========================================================
// LICENSE USER SELECT
// =========================================================

async function populateLicenseUsers() {
    const select =
        document.querySelector(
            "#create-license-form [name='user_id']"
        );

    if (!select) {
        return;
    }

    try {
        const data =
            await getUsers({
                limit: 200,
                status: "ACTIVE"
            });

        const users =
            data?.items || [];

        select.innerHTML = `
            <option value="">
                Select customer
            </option>
        `;

        users
            .filter(user => user.role === "CLIENT")
            .forEach(user => {
                const option =
                    document.createElement("option");

                option.value = user.id;
                option.textContent =
                    `${user.name} — ${user.email}`;

                select.appendChild(option);
            });

    } catch (error) {
        showError(error.message);
    }
}


// =========================================================
// STARTUP
// =========================================================

document.addEventListener(
    "DOMContentLoaded",
    async () => {
        setupLoginForm();
        setupNavigation();
        setupForms();
        setupButtons();

        if (accessToken) {
            showDashboard();
            await initializeDashboard();
        } else {
            showLogin();
        }
    }
);
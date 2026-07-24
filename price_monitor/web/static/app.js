const els = {
  authScreen: document.getElementById("authScreen"),
  appShell: document.getElementById("appShell"),
  authForm: document.getElementById("authForm"),
  authTabs: document.getElementById("authTabs"),
  authUser: document.getElementById("authUser"),
  authName: document.getElementById("authName"),
  authEmail: document.getElementById("authEmail"),
  authPhone: document.getElementById("authPhone"),
  authPass: document.getElementById("authPass"),
  authPassConfirm: document.getElementById("authPassConfirm"),
  confirmPassField: document.getElementById("confirmPassField"),
  rememberUserField: document.getElementById("rememberUserField"),
  authRemember: document.getElementById("authRemember"),
  authSubmit: document.getElementById("authSubmit"),
  authMsg: document.getElementById("authMsg"),
  authLinks: document.getElementById("authLinks"),
  forgotPasswordLink: document.getElementById("forgotPasswordLink"),
  backToLoginWrap: document.getElementById("backToLoginWrap"),
  backToLogin: document.getElementById("backToLogin"),
  usernameField: document.getElementById("usernameField"),
  passwordField: document.getElementById("passwordField"),
  forgotEmailField: document.getElementById("forgotEmailField"),
  forgotEmail: document.getElementById("forgotEmail"),
  registerFields: document.getElementById("registerFields"),
  nameField: document.getElementById("nameField"),
  usernameHint: document.getElementById("usernameHint"),
  userLabel: document.getElementById("userLabel"),
  btnLogout: document.getElementById("btnLogout"),
  list: document.getElementById("productList"),
  empty: document.getElementById("emptyState"),
  boardMeta: document.getElementById("boardMeta"),
  productSort: document.getElementById("productSort"),
  summary: document.getElementById("summary"),
  summaryPills: document.getElementById("summaryPills"),
  retailerFilter: document.getElementById("retailerFilter"),
  btnCheck: document.getElementById("btnCheck"),
  checkCooldownHint: document.getElementById("checkCooldownHint"),
  addForm: document.getElementById("addForm"),
  productNameInput: document.getElementById("productNameInput"),
  urlInput: document.getElementById("urlInput"),
  targetInput: document.getElementById("targetInput"),
  formMsg: document.getElementById("formMsg"),
  addProgress: document.getElementById("addProgress"),
  profileName: document.getElementById("profileName"),
  profileUsername: document.getElementById("profileUsername"),
  profileEmail: document.getElementById("profileEmail"),
  profilePhone: document.getElementById("profilePhone"),
  passwordForm: document.getElementById("passwordForm"),
  contactForm: document.getElementById("contactForm"),
  contactFormTitle: document.getElementById("contactFormTitle"),
  contactField: document.getElementById("contactField"),
  contactCurrentPassword: document.getElementById("contactCurrentPassword"),
  contactEmailField: document.getElementById("contactEmailField"),
  contactPhoneField: document.getElementById("contactPhoneField"),
  contactNewEmail: document.getElementById("contactNewEmail"),
  contactNewPhone: document.getElementById("contactNewPhone"),
  contactCode: document.getElementById("contactCode"),
  contactMsg: document.getElementById("contactMsg"),
  btnChangeEmail: document.getElementById("btnChangeEmail"),
  btnChangePhone: document.getElementById("btnChangePhone"),
  btnSendContactCode: document.getElementById("btnSendContactCode"),
  btnCancelContact: document.getElementById("btnCancelContact"),
  btnChangePassword: document.getElementById("btnChangePassword"),
  btnCancelPassword: document.getElementById("btnCancelPassword"),
  accountBody: document.getElementById("accountBody"),
  accountSubtitle: document.getElementById("accountSubtitle"),
  btnToggleAccount: document.getElementById("btnToggleAccount"),
  currentPassword: document.getElementById("currentPassword"),
  newPassword: document.getElementById("newPassword"),
  confirmNewPassword: document.getElementById("confirmNewPassword"),
  passwordMsg: document.getElementById("passwordMsg"),
};

let pollTimer = null;
let resetToken = null;
let checkCooldownTimer = null;
let authMode = "login";
let currentUser = null;
let usernameCheckTimer = null;
let usernameAvailable = null;
let currentProducts = [];
let checkAllowed = true;
let currentProfileEmail = "";
let currentProfilePhone = "";

const REMEMBER_USER_KEY = "pm_remember_username";
const REMEMBER_FLAG_KEY = "pm_remember_username_enabled";
const HIDE_ACCOUNT_KEY = "pm_hide_account";
const SORT_STORAGE_KEY = "pm_product_sort";
const DEFAULT_SORT = "added_desc";

function normalizeUrlKey(url) {
  return String(url || "")
    .trim()
    .replace(/\/+$/, "")
    .toLowerCase();
}

function urlAlreadyInList(url) {
  const key = normalizeUrlKey(url);
  if (!key) return false;
  return currentProducts.some((p) => normalizeUrlKey(p.url) === key);
}

/** Alphabet for the internal 20-char display token (A-Z + 0-9). */
const URL_MASK_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
const URL_MASK_LENGTH = 20;

/** Deterministic 20-char token from the product URL (stable across reloads). */
function buildUrlMaskToken(seed) {
  const raw = String(seed || "url");
  const chars = [];
  let state = 2166136261; // FNV-ish seed
  for (let i = 0; i < raw.length; i += 1) {
    state ^= raw.charCodeAt(i);
    state = Math.imul(state, 16777619);
  }
  for (let i = 0; i < URL_MASK_LENGTH; i += 1) {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    const idx = Math.abs(state) % URL_MASK_ALPHABET.length;
    chars.push(URL_MASK_ALPHABET[idx]);
  }
  return chars.join("");
}

function storeHostForRetailer(retailer, fallbackHost) {
  const store = String(retailer || "").toLowerCase();
  const map = {
    amazon: "amazon.com",
    walmart: "walmart.com",
    safeway: "safeway.com",
    instacart: "instacart.com",
    target: "target.com",
  };
  if (map[store]) return map[store];
  let host = String(fallbackHost || "")
    .toLowerCase()
    .replace(/^www\./, "");
  return host || "link.com";
}

/** Short display mask: www.store.com/<20-char A-Z0-9 token> (full URL stays in href). */
function maskProductUrl(url, retailer) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  let host = "";
  try {
    host = new URL(raw).hostname || "";
  } catch {
    host = "";
  }
  const displayHost = storeHostForRetailer(retailer, host);
  const token = buildUrlMaskToken(normalizeUrlKey(raw));
  return `www.${displayHost}/${token}`;
}

function formatRemaining(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}min`;
  if (minutes > 0) return `${minutes}min`;
  return "less than 1 min";
}

function applyCheckCooldown(checkCd) {
  if (checkCooldownTimer) {
    clearTimeout(checkCooldownTimer);
    checkCooldownTimer = null;
  }
  const data = checkCd || {};
  checkAllowed = data.allowed !== false;
  const remaining = Number(data.remaining_seconds) || 0;
  if (checkAllowed || remaining <= 0) {
    checkAllowed = true;
    els.btnCheck.disabled = false;
    els.btnCheck.removeAttribute("aria-disabled");
    els.btnCheck.textContent = "Check prices";
    if (els.checkCooldownHint) {
      els.checkCooldownHint.hidden = true;
      els.checkCooldownHint.textContent = "";
    }
    return;
  }
  els.btnCheck.disabled = true;
  els.btnCheck.setAttribute("aria-disabled", "true");
  els.btnCheck.textContent = "Check prices";
  if (els.checkCooldownHint) {
    els.checkCooldownHint.hidden = false;
    els.checkCooldownHint.textContent =
      `Next check in ${formatRemaining(remaining)}.`;
  }
  const waitMs = Math.min(Math.max(remaining, 1) * 1000, 60_000);
  checkCooldownTimer = setTimeout(async () => {
    try {
      await loadMeta();
    } catch {
      /* ignore */
    }
  }, waitMs);
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `$${Number(value).toFixed(2)}`;
}

function fmtTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("en-US");
  } catch {
    return iso;
  }
}

function statusLabel(last) {
  if (!last) return { text: "not checked", cls: "" };
  const map = {
    ok: { text: "no alert", cls: "ok" },
    unavailable: { text: "unavailable", cls: "unavailable" },
    alert: { text: "alert", cls: "alert" },
    cooldown: { text: "cooldown", cls: "cooldown" },
    error: { text: "error", cls: "error" },
  };
  return map[last.status] || { text: last.status || "—", cls: "" };
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || data.error || `HTTP ${res.status}`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return data;
}

function setUsernameHint(message, state) {
  els.usernameHint.textContent = message || "";
  els.usernameHint.classList.remove("is-error", "is-ok");
  els.authUser.classList.remove("is-invalid");
  if (state === "error") {
    els.usernameHint.classList.add("is-error");
    els.authUser.classList.add("is-invalid");
  } else if (state === "ok") {
    els.usernameHint.classList.add("is-ok");
  }
}

function clearUsernameCheck() {
  if (usernameCheckTimer) {
    clearTimeout(usernameCheckTimer);
    usernameCheckTimer = null;
  }
  usernameAvailable = null;
  setUsernameHint("", null);
}

async function checkUsernameAvailability() {
  if (authMode !== "register") {
    clearUsernameCheck();
    return;
  }
  const username = els.authUser.value.trim();
  if (username.length < 3) {
    usernameAvailable = null;
    setUsernameHint(username ? "Enter at least 3 characters." : "", username ? "error" : null);
    return;
  }
  try {
    const data = await api(
      `/api/auth/check-username?username=${encodeURIComponent(username)}`
    );
    usernameAvailable = data.available;
    if (data.available === false) {
      setUsernameHint(data.message || "That username is already taken.", "error");
    } else if (data.available === true) {
      setUsernameHint(data.message || "Username available.", "ok");
    } else {
      setUsernameHint(data.message || "", null);
    }
  } catch (err) {
    usernameAvailable = null;
    setUsernameHint("Could not check username.", "error");
  }
}

function scheduleUsernameCheck() {
  if (authMode !== "register") {
    clearUsernameCheck();
    return;
  }
  if (usernameCheckTimer) clearTimeout(usernameCheckTimer);
  usernameCheckTimer = setTimeout(checkUsernameAvailability, 350);
}

function setAuthMode(mode) {
  authMode = mode;
  const isRegister = mode === "register";
  const isForgot = mode === "forgot";
  const isReset = mode === "reset";
  const isLogin = mode === "login";

  document.querySelectorAll(".auth-tab").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.mode === mode);
  });
  if (els.authTabs) {
    els.authTabs.hidden = isForgot || isReset;
  }

  if (isForgot) {
    els.authSubmit.textContent = "Send link";
  } else if (isReset) {
    els.authSubmit.textContent = "Save new password";
  } else {
    els.authSubmit.textContent = isRegister ? "Create account" : "Sign in";
  }

  els.authUser.autocomplete = isRegister ? "off" : "username";
  els.authPass.autocomplete =
    isRegister || isReset ? "new-password" : "current-password";

  if (els.usernameField) els.usernameField.hidden = isForgot || isReset;
  if (els.passwordField) els.passwordField.hidden = isForgot;
  if (els.forgotEmailField) els.forgotEmailField.hidden = !isForgot;

  els.registerFields.hidden = !isRegister;
  els.confirmPassField.hidden = !(isRegister || isReset);
  if (els.nameField) {
    els.nameField.hidden = !isRegister;
    els.nameField.setAttribute("aria-hidden", isRegister ? "false" : "true");
  }
  els.registerFields.setAttribute("aria-hidden", isRegister ? "false" : "true");
  els.confirmPassField.setAttribute(
    "aria-hidden",
    isRegister || isReset ? "false" : "true"
  );

  els.authUser.required = isLogin || isRegister;
  els.authPass.required = isLogin || isRegister || isReset;
  els.authName.required = isRegister;
  els.authEmail.required = isRegister;
  els.authPhone.required = isRegister;
  els.authPassConfirm.required = isRegister || isReset;
  if (els.forgotEmail) els.forgotEmail.required = isForgot;

  els.authName.disabled = !isRegister;
  els.authEmail.disabled = !isRegister;
  els.authPhone.disabled = !isRegister;
  els.authPassConfirm.disabled = !(isRegister || isReset);
  els.authUser.disabled = isForgot || isReset;
  els.authPass.disabled = isForgot;
  if (els.forgotEmail) els.forgotEmail.disabled = !isForgot;

  if (els.rememberUserField) {
    els.rememberUserField.hidden = !isLogin;
  }
  if (els.authLinks) els.authLinks.hidden = !isLogin;
  if (els.backToLoginWrap) els.backToLoginWrap.hidden = isLogin || isRegister;

  if (isRegister) {
    els.authUser.value = "";
    els.authPass.value = "";
    els.authName.value = "";
    els.authEmail.value = "";
    els.authPhone.value = "";
    els.authPassConfirm.value = "";
    if (els.forgotEmail) els.forgotEmail.value = "";
    clearUsernameCheck();
  } else if (isForgot) {
    els.authPass.value = "";
    els.authPassConfirm.value = "";
    if (els.forgotEmail) els.forgotEmail.value = "";
    clearUsernameCheck();
  } else if (isReset) {
    els.authPass.value = "";
    els.authPassConfirm.value = "";
    clearUsernameCheck();
  } else {
    els.authName.value = "";
    els.authEmail.value = "";
    els.authPhone.value = "";
    els.authPassConfirm.value = "";
    if (els.forgotEmail) els.forgotEmail.value = "";
    applyRememberedUsername();
  }
  resetPasswordVisibility();
  els.authMsg.textContent = "";
  els.authMsg.classList.remove("error");

  const lede = document.querySelector(".auth-panel .lede");
  if (lede) {
    if (isForgot) {
      lede.textContent =
        "Enter the account email. We will send a password reset link.";
    } else if (isReset) {
      lede.textContent = "Choose a new password for your account.";
    } else {
      lede.textContent = "Sign in to view and manage your product list.";
    }
  }
}

function resetPasswordVisibility() {
  document.querySelectorAll(".password-toggle").forEach((btn) => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    input.type = "password";
    btn.textContent = "Show";
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", "Show password");
  });
}

document.querySelectorAll(".password-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    btn.textContent = showing ? "Show" : "Hide";
    btn.setAttribute("aria-pressed", showing ? "false" : "true");
    btn.setAttribute("aria-label", showing ? "Show password" : "Hide password");
  });
});

function formatPhoneMask(value) {
  const digits = String(value || "").replace(/\D/g, "").slice(0, 10);
  if (digits.length <= 3) return digits.length ? `(${digits}` : "";
  if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}

function formatMoneyMask(value) {
  // Up to 999,999,999,999,999.99 -> 17 digits (15 integer + 2 cents)
  let digits = String(value || "").replace(/\D/g, "").slice(0, 17);
  if (!digits) return "";
  digits = digits.replace(/^0+(?=\d)/, "");
  const padded = digits.padStart(3, "0");
  const whole = padded.slice(0, -2).replace(/^0+(?=\d)/, "") || "0";
  const frac = padded.slice(-2);
  const withThousands = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${withThousands}.${frac}`;
}

function parseMoneyValue(value) {
  const normalized = String(value || "").replace(/,/g, "").trim();
  if (!normalized) return NaN;
  // Keep precision for large values on submit (API accepts float).
  const num = Number(normalized);
  return Number.isFinite(num) ? num : NaN;
}

els.authPhone.addEventListener("input", () => {
  els.authPhone.value = formatPhoneMask(els.authPhone.value);
});

els.targetInput.addEventListener("input", () => {
  els.targetInput.value = formatMoneyMask(els.targetInput.value);
});

els.targetInput.addEventListener("blur", () => {
  if (!els.targetInput.value.trim()) return;
  els.targetInput.value = formatMoneyMask(els.targetInput.value);
});

els.authUser.addEventListener("input", () => {
  if (authMode === "register") scheduleUsernameCheck();
  else clearUsernameCheck();
});

els.authUser.addEventListener("blur", () => {
  if (authMode === "register") checkUsernameAvailability();
});

function showAuth(mode) {
  currentUser = null;
  els.authScreen.hidden = false;
  els.appShell.hidden = true;
  if (mode) {
    setAuthMode(mode);
  } else if (authMode !== "forgot" && authMode !== "reset") {
    setAuthMode("login");
  }
  if (authMode === "login") applyRememberedUsername();
}

function clearAuthSecrets() {
  els.authPass.value = "";
  els.authPassConfirm.value = "";
  els.authName.value = "";
  els.authEmail.value = "";
  els.authPhone.value = "";
  resetPasswordVisibility();
}

function saveRememberPreference(username) {
  if (els.authRemember.checked && username) {
    localStorage.setItem(REMEMBER_FLAG_KEY, "1");
    localStorage.setItem(REMEMBER_USER_KEY, username);
  } else {
    localStorage.removeItem(REMEMBER_FLAG_KEY);
    localStorage.removeItem(REMEMBER_USER_KEY);
  }
}

function applyRememberedUsername() {
  const enabled = localStorage.getItem(REMEMBER_FLAG_KEY) === "1";
  const saved = localStorage.getItem(REMEMBER_USER_KEY) || "";
  els.authRemember.checked = enabled;
  if (enabled && saved) {
    els.authUser.value = saved;
  } else {
    els.authUser.value = "";
  }
  clearAuthSecrets();
  clearUsernameCheck();
}

function showApp(username, displayName) {
  currentUser = username;
  els.userLabel.textContent = displayName || username || "—";
  els.authScreen.hidden = true;
  els.appShell.hidden = false;
  applyAccountVisibility();
}

function applyAccountVisibility() {
  if (!els.accountBody || !els.btnToggleAccount) return;
  const hidden = localStorage.getItem(HIDE_ACCOUNT_KEY) === "1";
  els.accountBody.hidden = hidden;
  els.btnToggleAccount.setAttribute("aria-expanded", hidden ? "false" : "true");
  els.btnToggleAccount.textContent = hidden ? "Show details" : "Hide details";
  if (els.accountSubtitle) {
    els.accountSubtitle.textContent = hidden
      ? "Details hidden."
      : "Your details and contact settings.";
  }
}

if (els.btnToggleAccount) {
  els.btnToggleAccount.addEventListener("click", () => {
    const willHide = !els.accountBody.hidden;
    if (willHide) localStorage.setItem(HIDE_ACCOUNT_KEY, "1");
    else localStorage.removeItem(HIDE_ACCOUNT_KEY);
    applyAccountVisibility();
  });
}

function resetPasswordFormFields() {
  if (!els.passwordForm) return;
  els.passwordForm.reset();
  if (els.passwordMsg) {
    els.passwordMsg.textContent = "";
    els.passwordMsg.classList.remove("error");
  }
  document.querySelectorAll("#passwordForm .password-toggle").forEach((btn) => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    input.type = "password";
    btn.textContent = "Show";
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", "Show password");
  });
}

function setAccountActionButtonsVisible(visible) {
  [els.btnChangeEmail, els.btnChangePhone, els.btnChangePassword].forEach((btn) => {
    if (btn) btn.hidden = !visible;
  });
}

function openPasswordForm() {
  if (!els.passwordForm) return;
  closeContactForm();
  if (els.accountBody?.hidden) {
    localStorage.removeItem(HIDE_ACCOUNT_KEY);
    applyAccountVisibility();
  }
  els.passwordForm.hidden = false;
  setAccountActionButtonsVisible(false);
  els.passwordMsg.textContent = "";
  els.passwordMsg.classList.remove("error");
  els.currentPassword?.focus();
}

function closePasswordForm() {
  if (!els.passwordForm) return;
  els.passwordForm.hidden = true;
  setAccountActionButtonsVisible(true);
  resetPasswordFormFields();
}

function resetContactFormFields() {
  if (!els.contactForm) return;
  if (els.contactCurrentPassword) els.contactCurrentPassword.value = "";
  if (els.contactNewEmail) els.contactNewEmail.value = "";
  if (els.contactNewPhone) els.contactNewPhone.value = "";
  if (els.contactCode) els.contactCode.value = "";
  if (els.contactMsg) {
    els.contactMsg.textContent = "";
    els.contactMsg.classList.remove("error");
  }
  document.querySelectorAll("#contactForm .password-toggle").forEach((btn) => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    input.type = "password";
    btn.textContent = "Show";
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-label", "Show password");
  });
  const emailRadio = els.contactForm.querySelector(
    'input[name="contactChannel"][value="email"]'
  );
  if (emailRadio) emailRadio.checked = true;
}

function openContactForm(field) {
  if (!els.contactForm) return;
  closePasswordForm();
  if (els.accountBody?.hidden) {
    localStorage.removeItem(HIDE_ACCOUNT_KEY);
    applyAccountVisibility();
  }
  const isEmail = field === "email";
  resetContactFormFields();
  els.contactField.value = isEmail ? "email" : "phone";
  if (els.contactFormTitle) {
    els.contactFormTitle.textContent = isEmail ? "Change email" : "Change phone";
  }
  if (els.contactEmailField) els.contactEmailField.hidden = !isEmail;
  if (els.contactPhoneField) els.contactPhoneField.hidden = isEmail;
  if (els.contactNewEmail) {
    els.contactNewEmail.required = isEmail;
    els.contactNewEmail.disabled = !isEmail;
  }
  if (els.contactNewPhone) {
    els.contactNewPhone.required = !isEmail;
    els.contactNewPhone.disabled = isEmail;
  }
  els.contactForm.hidden = false;
  setAccountActionButtonsVisible(false);
  els.contactCurrentPassword?.focus();
}

function closeContactForm() {
  if (!els.contactForm) return;
  els.contactForm.hidden = true;
  setAccountActionButtonsVisible(true);
  resetContactFormFields();
}

function selectedContactChannel() {
  const checked = els.contactForm?.querySelector('input[name="contactChannel"]:checked');
  return checked?.value === "phone" ? "phone" : "email";
}

function contactNewValue() {
  const field = els.contactField?.value === "phone" ? "phone" : "email";
  if (field === "phone") return (els.contactNewPhone?.value || "").trim();
  return (els.contactNewEmail?.value || "").trim();
}

function normalizePhoneDigits(value) {
  return String(value || "").replace(/\D/g, "");
}

function isSameAsCurrentContact(field, newValue) {
  if (field === "phone") {
    const next = normalizePhoneDigits(newValue);
    const current = normalizePhoneDigits(currentProfilePhone);
    return Boolean(next) && next === current && current.length === 10;
  }
  const next = String(newValue || "").trim().toLowerCase();
  const current = String(currentProfileEmail || "").trim().toLowerCase();
  return Boolean(next) && next === current;
}

function sameContactMessage(field) {
  return field === "phone"
    ? "This is your current phone."
    : "This is your current email.";
}

if (els.btnChangePassword) {
  els.btnChangePassword.addEventListener("click", () => openPasswordForm());
}
if (els.btnCancelPassword) {
  els.btnCancelPassword.addEventListener("click", () => closePasswordForm());
}
if (els.btnChangeEmail) {
  els.btnChangeEmail.addEventListener("click", () => openContactForm("email"));
}
if (els.btnChangePhone) {
  els.btnChangePhone.addEventListener("click", () => openContactForm("phone"));
}
if (els.btnCancelContact) {
  els.btnCancelContact.addEventListener("click", () => closeContactForm());
}
if (els.contactNewPhone) {
  els.contactNewPhone.addEventListener("input", () => {
    els.contactNewPhone.value = formatPhoneMask(els.contactNewPhone.value);
    if (!els.contactMsg) return;
    if (isSameAsCurrentContact("phone", els.contactNewPhone.value)) {
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = sameContactMessage("phone");
    } else if (els.contactMsg.textContent === sameContactMessage("phone")) {
      els.contactMsg.textContent = "";
      els.contactMsg.classList.remove("error");
    }
  });
}
if (els.contactNewEmail) {
  els.contactNewEmail.addEventListener("input", () => {
    if (!els.contactMsg) return;
    if (isSameAsCurrentContact("email", els.contactNewEmail.value)) {
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = sameContactMessage("email");
    } else if (els.contactMsg.textContent === sameContactMessage("email")) {
      els.contactMsg.textContent = "";
      els.contactMsg.classList.remove("error");
    }
  });
}
if (els.btnSendContactCode) {
  els.btnSendContactCode.addEventListener("click", async () => {
    if (!els.contactMsg) return;
    els.contactMsg.textContent = "";
    els.contactMsg.classList.remove("error");
    const field = els.contactField?.value === "phone" ? "phone" : "email";
    const currentPassword = els.contactCurrentPassword?.value || "";
    const newValue = contactNewValue();
    const channel = selectedContactChannel();
    if (!currentPassword || !newValue) {
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = "Fill in the current password and the new value.";
      return;
    }
    if (isSameAsCurrentContact(field, newValue)) {
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = sameContactMessage(field);
      return;
    }
    if (field === "phone" && newValue.replace(/\D/g, "").length !== 10) {
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = "Invalid phone. Use the format (xxx) xxx-xxxx.";
      return;
    }
    try {
      els.btnSendContactCode.disabled = true;
      const result = await api("/api/auth/change-contact/request", {
        method: "POST",
        body: JSON.stringify({
          field,
          new_value: newValue,
          current_password: currentPassword,
          channel,
        }),
      });
      els.contactMsg.textContent =
        result.message || `Verification code sent via ${channel}.`;
      els.contactCode?.focus();
    } catch (err) {
      if (err.status === 401) return showAuth();
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = err.message;
    } finally {
      els.btnSendContactCode.disabled = false;
    }
  });
}
if (els.contactForm) {
  els.contactForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    els.contactMsg.textContent = "";
    els.contactMsg.classList.remove("error");
    const field = els.contactField?.value === "phone" ? "phone" : "email";
    const currentPassword = els.contactCurrentPassword?.value || "";
    const newValue = contactNewValue();
    const code = (els.contactCode?.value || "").trim();
    if (!currentPassword || !newValue || !code) {
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = "Fill in password, new value, and verification code.";
      return;
    }
    if (isSameAsCurrentContact(field, newValue)) {
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = sameContactMessage(field);
      return;
    }
    try {
      const result = await api("/api/auth/change-contact/confirm", {
        method: "POST",
        body: JSON.stringify({
          field,
          new_value: newValue,
          current_password: currentPassword,
          code,
        }),
      });
      els.contactMsg.textContent = result.message || "Contact updated successfully.";
      await loadProfile();
      if (result.user?.name || result.user?.username) {
        const displayName = result.user.name || result.user.username;
        if (els.userLabel) els.userLabel.textContent = displayName;
      }
      setTimeout(() => closeContactForm(), 900);
    } catch (err) {
      if (err.status === 401) return showAuth();
      els.contactMsg.classList.add("error");
      els.contactMsg.textContent = err.message;
    }
  });
}

function getProductSort() {
  const saved = (localStorage.getItem(SORT_STORAGE_KEY) || "").trim();
  if (saved && els.productSort?.querySelector(`option[value="${saved}"]`)) {
    return saved;
  }
  return DEFAULT_SORT;
}

function setProductSort(value) {
  const next = value || DEFAULT_SORT;
  localStorage.setItem(SORT_STORAGE_KEY, next);
  if (els.productSort) els.productSort.value = next;
}

function productAlertStatus(product) {
  if (product.pending) return "new store · available in 24h";
  return statusLabel(product.last_check).text || "—";
}

function productSortValue(product, mode) {
  if (mode.startsWith("name_")) {
    return String(product.name || "").trim().toLowerCase();
  }
  if (mode.startsWith("store_")) {
    return String(product.brand || product.retailer || "")
      .trim()
      .toLowerCase();
  }
  if (mode.startsWith("price_")) {
    const current = product.last_check?.current_price;
    if (current != null && Number.isFinite(Number(current))) return Number(current);
    return null;
  }
  if (mode.startsWith("delta_")) {
    if (product.delta_to_target == null || !Number.isFinite(Number(product.delta_to_target))) {
      return null;
    }
    return Math.abs(Number(product.delta_to_target));
  }
  if (mode.startsWith("status_")) {
    return productAlertStatus(product).toLowerCase();
  }
  // added_*
  return String(product.added_at || "");
}

function sortProducts(products, mode) {
  const list = Array.isArray(products) ? products.slice() : [];
  const desc = mode.endsWith("_desc");
  list.sort((a, b) => {
    const av = productSortValue(a, mode);
    const bv = productSortValue(b, mode);
    const aMissing = av == null || av === "";
    const bMissing = bv == null || bv === "";
    if (aMissing && bMissing) {
      return (Number(a.index) || 0) - (Number(b.index) || 0);
    }
    if (aMissing) return 1;
    if (bMissing) return -1;
    let cmp = 0;
    if (typeof av === "number" && typeof bv === "number") {
      cmp = av - bv;
    } else {
      cmp = String(av).localeCompare(String(bv), undefined, {
        numeric: true,
        sensitivity: "base",
      });
    }
    if (cmp === 0) {
      cmp = (Number(a.index) || 0) - (Number(b.index) || 0);
    }
    return desc ? -cmp : cmp;
  });
  return list;
}

function renderProducts(products) {
  currentProducts = Array.isArray(products) ? products : [];
  const sortMode = getProductSort();
  if (els.productSort) els.productSort.value = sortMode;
  const sorted = sortProducts(currentProducts, sortMode);
  els.list.innerHTML = "";
  els.empty.hidden = sorted.length > 0;
  els.boardMeta.textContent = `${sorted.length} item(s)`;

  let below = 0;
  let known = 0;
  let unavailable = 0;

  sorted.forEach((p) => {
    const last = p.last_check;
    if (last?.current_price != null) known += 1;
    if (p.below_target) below += 1;
    if (last?.status === "unavailable") unavailable += 1;

    const st = p.pending
      ? { text: "new store · available in 24h", cls: "cooldown" }
      : statusLabel(last);
    const removeIndex = Number.isInteger(p.index) ? p.index : -1;
    const row = document.createElement("article");
    row.className = "product";
    row.innerHTML = `
      <div>
        <span class="store">${escapeHtml(p.brand || p.retailer)}</span>
        <h3 class="name">${escapeHtml(p.name || "Unnamed")}</h3>
        <p class="product-url"><a href="${escapeHtml(p.url)}" target="_blank" rel="noopener" title="${escapeHtml(p.url)}">${escapeHtml(maskProductUrl(p.url, p.retailer))}</a></p>
        <p class="meta">${
          p.pending
            ? "New store — available in 24 hours."
            : last?.checked_at
              ? `checked ${fmtTime(last.checked_at)}`
              : "not checked yet"
        }${last?.error ? ` · ${escapeHtml(last.error)}` : ""}</p>
      </div>
      <div class="prices">
        <div class="current ${p.below_target ? "good" : last?.current_price != null ? "bad" : ""}">${money(last?.current_price)}</div>
        <div class="target">target ${money(p.target_price)}</div>
        <div class="delta">${p.delta_to_target == null ? "" : (p.delta_to_target <= 0 ? "" : "+") + money(p.delta_to_target).replace("$", "") + " vs target"}</div>
      </div>
      <div class="status ${st.cls}">
        ${st.text}
        <div><button type="button" class="btn ghost" data-remove="${removeIndex}">remove</button></div>
      </div>
    `;
    els.list.appendChild(row);
  });

  els.summary.hidden = false;
  const pills = els.summaryPills || els.summary;
  pills.innerHTML = `
    <div class="pill"><strong>${sorted.length}</strong> products</div>
    <div class="pill"><strong>${known}</strong> with price</div>
    <div class="pill"><strong>${below}</strong> at or below target</div>
    <div class="pill"><strong>${unavailable}</strong> unavailable</div>
  `;

  els.list.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const idx = Number(btn.getAttribute("data-remove"));
      if (!Number.isInteger(idx) || idx < 0) return;
      if (!confirm("Remove this product from your list?")) return;
      try {
        await api(`/api/products/${idx}`, { method: "DELETE" });
        await loadProducts();
        if (els.formMsg) {
          els.formMsg.classList.remove("error");
          els.formMsg.textContent = "Product removed.";
        }
      } catch (err) {
        if (err.status === 401) return showAuth();
        if (els.formMsg) {
          els.formMsg.classList.add("error");
          els.formMsg.textContent = err.message;
        } else {
          alert(err.message);
        }
      }
    });
  });
}

async function loadMeta() {
  const meta = await api("/api/meta");
  const select = els.retailerFilter;
  const current = select.value;
  select.innerHTML = `<option value="">all</option>`;
  for (const r of meta.retailers || []) {
    const opt = document.createElement("option");
    opt.value = r;
    opt.textContent = r;
    select.appendChild(opt);
  }
  select.value = current;
  applyCheckCooldown(meta.check_cooldown);
}

async function loadProducts() {
  const data = await api("/api/products");
  renderProducts(data.products || []);
}

function fillProfile(user) {
  if (!user) return;
  if (els.profileName) els.profileName.textContent = user.name || "—";
  if (els.profileUsername) els.profileUsername.textContent = user.username || "—";
  if (els.profileEmail) els.profileEmail.textContent = user.email || "—";
  if (els.profilePhone) els.profilePhone.textContent = user.phone || "—";
  currentProfileEmail = user.email || "";
  currentProfilePhone = user.phone || "";
  if (els.userLabel) {
    els.userLabel.textContent = user.name || user.username || "—";
  }
}

async function loadProfile() {
  const data = await api("/api/auth/profile");
  fillProfile(data.user || {});
}

async function enterApp(username, user) {
  showApp(username, user?.name || username);
  if (user) fillProfile(user);
  await loadMeta();
  await loadProducts();
  await loadProfile();
}

async function pollJob(jobId) {
  while (true) {
    const job = await api(`/api/check/${jobId}`);
    if (job.status === "running") {
      await new Promise((resolve) => {
        pollTimer = setTimeout(resolve, 1200);
      });
      continue;
    }
    await loadProducts();
    await loadMeta();
    return job;
  }
}

function setAddProgress(running) {
  if (!els.addProgress) return;
  els.addProgress.hidden = !running;
  els.addProgress.setAttribute("aria-hidden", running ? "false" : "true");
}

document.querySelectorAll(".auth-tab").forEach((btn) => {
  btn.addEventListener("click", () => setAuthMode(btn.dataset.mode));
});

els.authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.authMsg.textContent = "";
  els.authMsg.classList.remove("error");

  if (authMode === "forgot") {
    const email = (els.forgotEmail?.value || "").trim();
    if (!email) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "Enter the account email.";
      return;
    }
    try {
      els.authSubmit.disabled = true;
      const result = await api("/api/auth/forgot-password", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      els.authMsg.textContent =
        result.message ||
        "If that email is registered, you will receive a link.";
    } catch (err) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = err.message;
    } finally {
      els.authSubmit.disabled = false;
    }
    return;
  }

  if (authMode === "reset") {
    const password = els.authPass.value;
    const confirm = els.authPassConfirm.value;
    if (!password || !confirm) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "Fill in all required fields.";
      return;
    }
    if (password !== confirm) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "Passwords do not match.";
      return;
    }
    if (!resetToken) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "Invalid or expired reset link.";
      return;
    }
    try {
      els.authSubmit.disabled = true;
      const result = await api("/api/auth/reset-password", {
        method: "POST",
        body: JSON.stringify({
          token: resetToken,
          new_password: password,
          confirm_password: confirm,
        }),
      });
      resetToken = null;
      const url = new URL(window.location.href);
      url.searchParams.delete("reset");
      window.history.replaceState({}, "", url.pathname + url.search);
      setAuthMode("login");
      els.authMsg.textContent =
        result.message || "Password reset. Sign in with your new password.";
    } catch (err) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = err.message;
    } finally {
      els.authSubmit.disabled = false;
    }
    return;
  }

  const username = els.authUser.value.trim();
  const password = els.authPass.value;
  if (!username || !password) {
    els.authMsg.classList.add("error");
    els.authMsg.textContent = "Fill in all required fields.";
    return;
  }

  if (authMode === "register") {
    const name = els.authName.value.trim();
    const email = els.authEmail.value.trim();
    const phone = els.authPhone.value.trim();
    const confirm = els.authPassConfirm.value;
    if (!name || !email || !phone || !confirm) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "Fill in all required fields.";
      return;
    }
    if (password !== confirm) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "Passwords do not match.";
      return;
    }
    const phoneDigits = phone.replace(/\D/g, "");
    if (phoneDigits.length !== 10) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "Invalid phone. Use the format (xxx) xxx-xxxx.";
      return;
    }
    await checkUsernameAvailability();
    if (usernameAvailable === false) {
      els.authMsg.classList.add("error");
      els.authMsg.textContent = "That username is already taken.";
      return;
    }
  }

  const payload = { username, password };
  if (authMode === "register") {
    payload.name = els.authName.value.trim();
    payload.email = els.authEmail.value.trim();
    payload.phone = els.authPhone.value.trim();
  }
  const endpoint = authMode === "login" ? "/api/auth/login" : "/api/auth/register";
  try {
    els.authSubmit.disabled = true;
    const result = await api(endpoint, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    els.authPass.value = "";
    els.authPassConfirm.value = "";
    resetPasswordVisibility();
    clearUsernameCheck();
    // Remember username only on sign-in; registration does not save the field.
    if (authMode === "login") {
      saveRememberPreference(result.user.username);
    }
    if (result.imported_legacy) {
      els.authMsg.textContent = "Account created and produtos.json imported.";
    }
    await enterApp(result.user.username, result.user);
  } catch (err) {
    els.authMsg.classList.add("error");
    els.authMsg.textContent = err.message;
  } finally {
    els.authSubmit.disabled = false;
  }
});

if (els.forgotPasswordLink) {
  els.forgotPasswordLink.addEventListener("click", () => setAuthMode("forgot"));
}
if (els.backToLogin) {
  els.backToLogin.addEventListener("click", () => {
    resetToken = null;
    setAuthMode("login");
  });
}

els.btnLogout.addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch {
    /* ignore */
  }
  // If not remembering, clear the username; password is always cleared.
  if (!els.authRemember.checked) {
    localStorage.removeItem(REMEMBER_FLAG_KEY);
    localStorage.removeItem(REMEMBER_USER_KEY);
  } else if (currentUser) {
    localStorage.setItem(REMEMBER_FLAG_KEY, "1");
    localStorage.setItem(REMEMBER_USER_KEY, currentUser);
  }
  showAuth();
});

els.passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.passwordMsg.textContent = "";
  els.passwordMsg.classList.remove("error");
  const currentPassword = els.currentPassword.value;
  const newPassword = els.newPassword.value;
  const confirmPassword = els.confirmNewPassword.value;
  if (!currentPassword || !newPassword || !confirmPassword) {
    els.passwordMsg.classList.add("error");
    els.passwordMsg.textContent = "Fill in all password fields.";
    return;
  }
  if (newPassword !== confirmPassword) {
    els.passwordMsg.classList.add("error");
    els.passwordMsg.textContent = "New passwords do not match.";
    return;
  }
  try {
    const result = await api("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      }),
    });
    els.passwordForm.reset();
    document.querySelectorAll("#passwordForm .password-toggle").forEach((btn) => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      input.type = "password";
      btn.textContent = "Show";
      btn.setAttribute("aria-pressed", "false");
    });
    els.passwordMsg.textContent = result.message || "Password changed successfully.";
    setTimeout(() => closePasswordForm(), 1200);
  } catch (err) {
    if (err.status === 401) return showAuth();
    els.passwordMsg.classList.add("error");
    els.passwordMsg.textContent = err.message;
  }
});

if (els.productSort) {
  els.productSort.value = getProductSort();
  els.productSort.addEventListener("change", () => {
    setProductSort(els.productSort.value);
    renderProducts(currentProducts);
  });
}

els.btnCheck.addEventListener("click", async () => {
  if (!checkAllowed || els.btnCheck.disabled) return;
  els.btnCheck.disabled = true;
  try {
    const body = {};
    if (els.retailerFilter.value) body.retailer = els.retailerFilter.value;
    const started = await api("/api/check", {
      method: "POST",
      body: JSON.stringify(body),
    });
    applyCheckCooldown(started.check_cooldown || { allowed: false, remaining_seconds: 24 * 3600 });
    if (pollTimer) clearTimeout(pollTimer);
    await pollJob(started.job_id);
  } catch (err) {
    if (err.status === 401) return showAuth();
    if (els.checkCooldownHint) {
      els.checkCooldownHint.hidden = false;
      els.checkCooldownHint.textContent = err.message;
    }
    try {
      await loadMeta();
    } catch {
      els.btnCheck.disabled = false;
    }
  }
});

els.addForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.formMsg.textContent = "";
  els.formMsg.classList.remove("error");
  const url = els.urlInput.value.trim();
  if (urlAlreadyInList(url)) {
    els.formMsg.classList.add("error");
    els.formMsg.textContent = "That URL is already in your product list.";
    return;
  }
  const targetPrice = parseMoneyValue(els.targetInput.value);
  if (!Number.isFinite(targetPrice) || targetPrice <= 0) {
    els.formMsg.classList.add("error");
    els.formMsg.textContent = "Enter a valid target price (e.g. 5.00).";
    return;
  }
  setAddProgress(false);
  try {
    const payload = {
      url,
      target_price: targetPrice,
    };
    const productName = (els.productNameInput?.value || "").trim();
    if (productName) payload.name = productName;
    const result = await api("/api/products", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (result.pending_store) {
      els.formMsg.textContent =
        "Product added. New store — available in 24 hours.";
    } else if (result.check_message) {
      els.formMsg.textContent = result.check_message;
    } else {
      els.formMsg.textContent = "Product added.";
    }
    els.addForm.reset();
    await loadProducts();
    if (result.check_started && result.job_id) {
      els.formMsg.textContent = "Product added.";
      setAddProgress(true);
      if (pollTimer) clearTimeout(pollTimer);
      const jobId = result.job_id;
      const shownAt = Date.now();
      try {
        await pollJob(jobId);
        const waitMs = 700 - (Date.now() - shownAt);
        if (waitMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, waitMs));
        }
        els.formMsg.textContent = "Product added and price checked.";
      } catch (err) {
        if (err.status === 401) return showAuth();
        els.formMsg.classList.add("error");
        els.formMsg.textContent =
          err.message || "Product added, but the check failed.";
      }
    }
  } catch (err) {
    if (err.status === 401) return showAuth();
    els.formMsg.classList.add("error");
    els.formMsg.textContent = err.message;
  } finally {
    setAddProgress(false);
  }
});

(async function init() {
  const params = new URLSearchParams(window.location.search);
  const tokenFromUrl = (params.get("reset") || "").trim();
  if (tokenFromUrl) {
    resetToken = tokenFromUrl;
    const url = new URL(window.location.href);
    url.searchParams.delete("reset");
    window.history.replaceState({}, "", url.pathname + url.search);
    showAuth("reset");
    return;
  }

  setAuthMode("login");
  applyRememberedUsername();
  try {
    const me = await api("/api/auth/me");
    if (me.authenticated && me.user?.username) {
      await enterApp(me.user.username, me.user);
      return;
    }
  } catch {
    /* fall through */
  }
  showAuth();
})();

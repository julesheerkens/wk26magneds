/**
 * Kiosk inactivity auto-logout.
 * Reads timeout from data-timeout on <body> (seconds).
 */
(function () {
  var body = document.body;
  var timeoutSeconds = parseInt(body.dataset.timeout || "15", 10);
  var warnAt = 5; // show warning this many seconds before logout
  var timer = null;
  var warnTimer = null;
  var banner = null;

  function createBanner() {
    banner = document.createElement("div");
    banner.id = "kiosk-logout-banner";
    banner.style.cssText =
      "position:fixed;bottom:0;left:0;right:0;background:#e53e3e;color:#fff;" +
      "text-align:center;padding:12px 16px;font-size:1rem;z-index:9999;display:none;";
    document.body.appendChild(banner);
  }

  function showWarning(secs) {
    if (!banner) createBanner();
    banner.textContent = "Automatisch uitloggen over " + secs + " seconde" + (secs === 1 ? "" : "n") + "…";
    banner.style.display = "block";
  }

  function hideBanner() {
    if (banner) banner.style.display = "none";
  }

  function doLogout() {
    // POST to /logout so session is cleared server-side
    var form = document.createElement("form");
    form.method = "POST";
    form.action = "/logout";
    // CSRF token not required for session clear in this app
    document.body.appendChild(form);
    form.submit();
  }

  function resetTimer() {
    clearTimeout(timer);
    clearTimeout(warnTimer);
    hideBanner();

    var warnDelay = (timeoutSeconds - warnAt) * 1000;
    if (warnDelay > 0) {
      warnTimer = setTimeout(function () {
        showWarning(warnAt);
        // Count down visually
        var remaining = warnAt - 1;
        var countdown = setInterval(function () {
          if (remaining <= 0) {
            clearInterval(countdown);
            return;
          }
          showWarning(remaining);
          remaining--;
        }, 1000);
      }, warnDelay);
    }

    timer = setTimeout(doLogout, timeoutSeconds * 1000);
  }

  function init() {
    // Only activate on pages where user is logged in (body has data-timeout)
    if (!body.dataset.timeout) return;

    createBanner();
    ["touchstart", "touchmove", "click", "keydown", "scroll", "mousemove"].forEach(function (evt) {
      document.addEventListener(evt, resetTimer, { passive: true });
    });
    resetTimer();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

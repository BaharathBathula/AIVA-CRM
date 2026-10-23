/*!
 * AIVA website visits tracker.
 * <script async src="https://YOUR-AIVA/aiva-track.js" data-site="ws_..."></script>
 * Records page views and time on page, and links the visitor to a lead or
 * contact when they submit a form with an email address (or when you call
 * window.aiva.identify(email, name)). Uses first-party storage only.
 */
(function () {
  var script = document.currentScript || document.querySelector('script[data-site][src*="aiva-track"]');
  if (!script) return;
  var site = script.getAttribute("data-site");
  if (!site || window.__aivaTracking) return;
  window.__aivaTracking = true;
  if (navigator.doNotTrack === "1" && script.getAttribute("data-respect-dnt") !== "false") return;

  var endpoint = new URL(script.src, location.href).origin + "/api/aiva/public/track";
  var SESSION_GAP = 30 * 60 * 1000;

  function newId() {
    var raw = window.crypto && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
    return raw.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40);
  }
  function read(key) {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }
  function write(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      /* storage blocked: the visit still counts, just not across pages */
    }
  }

  var visitor = read("aiva_vid");
  if (!visitor || visitor.length < 8) {
    visitor = newId();
    write("aiva_vid", visitor);
  }
  var now = Date.now();
  var session = read("aiva_sid");
  var last = parseInt(read("aiva_slast") || "0", 10);
  if (!session || now - last > SESSION_GAP) {
    session = newId();
    write("aiva_sid", session);
  }
  write("aiva_slast", String(now));

  var view = newId();
  var visible = 0;
  var lastTick = Date.now();

  function send(event, extra) {
    var payload = {
      k: site,
      v: visitor,
      s: session,
      e: event,
      p: view,
      u: location.href,
      t: document.title,
      r: document.referrer,
      lang: navigator.language,
      tz: (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone || "",
    };
    for (var key in extra || {}) payload[key] = extra[key];
    var body = JSON.stringify(payload);
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(endpoint, new Blob([body], { type: "text/plain" }))) return;
    } catch (e) {
      /* fall through to fetch */
    }
    try {
      fetch(endpoint, { method: "POST", body: body, headers: { "Content-Type": "text/plain" }, keepalive: true, mode: "no-cors" });
    } catch (e) {
      /* offline */
    }
  }

  function tick() {
    var at = Date.now();
    if (document.visibilityState === "visible") visible += at - lastTick;
    lastTick = at;
    write("aiva_slast", String(at));
  }
  function ping() {
    tick();
    send("ping", { d: Math.round(visible / 1000) });
  }

  send("view");
  setInterval(function () {
    if (document.visibilityState === "visible") ping();
  }, 15000);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") ping();
    else lastTick = Date.now();
  });

  // Single-page apps: count each route change as a page view.
  function route() {
    ping();
    view = newId();
    visible = 0;
    lastTick = Date.now();
    send("view");
  }
  var push = history.pushState;
  history.pushState = function () {
    var result = push.apply(this, arguments);
    setTimeout(route, 0);
    return result;
  };
  window.addEventListener("popstate", route);

  // A form with an email address tells AIVA who the visitor is.
  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      if (!form || !form.querySelector) return;
      var email = form.querySelector('input[type="email"], input[name*="email" i]');
      if (!email || !email.value || email.value.indexOf("@") < 1) return;
      var name = form.querySelector('input[name="name" i], input[name*="full_name" i], input[name*="fullname" i]');
      var first = form.querySelector('input[name*="first" i]');
      var lastName = form.querySelector('input[name*="last" i]');
      var full = name ? name.value : [first && first.value, lastName && lastName.value].filter(Boolean).join(" ");
      send("identify", { email: email.value, name: full || "" });
    },
    true
  );

  window.aiva = window.aiva || {};
  window.aiva.identify = function (email, name) {
    if (email) send("identify", { email: String(email), name: name ? String(name) : "" });
  };
})();

/* RecallAI sign-in for the plain HTML pages (gps.html, search.html).
 *
 *   RecallAuth.require(user => start(user))   // shows the sign-in form until signed in
 *   RecallAuth.fetch(url, options)             // adds Authorization: Bearer <token>
 *   RecallAuth.media("/snapshots/x.jpg")       // <img> URL with ?token= (imgs can't send headers)
 *   RecallAuth.signOut()
 *
 * The token lives in localStorage under "recallai.auth", the same key the
 * live dashboard uses, so signing in once covers every page on this origin.
 * A 401 from the API signs the page out and shows the form again.
 */
(function () {
  "use strict";
  var KEY = "recallai.auth";
  var USERNAME = /^[a-z0-9_.-]{3,32}$/;
  var ready = null;

  function load() {
    try {
      var s = JSON.parse(localStorage.getItem(KEY) || "null");
      return s && typeof s.token === "string" ? s : null;
    } catch (e) {
      return null;
    }
  }
  function save(s) {
    try {
      if (s) localStorage.setItem(KEY, JSON.stringify(s));
      else localStorage.removeItem(KEY);
    } catch (e) { /* private mode: this tab only */ }
    session = s;
  }
  var session = load();

  function detail(body, fallback) {
    var d = body && body.detail;
    if (typeof d === "string") return d;
    if (d && typeof d.message === "string") return d.message;
    if (Array.isArray(d) && d.length) return d.map(function (x) { return x.msg || String(x); }).join(" ");
    return fallback;
  }

  function authFetch(url, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    if (session) headers.Authorization = "Bearer " + session.token;
    return fetch(url, Object.assign({}, options, { headers: headers })).then(function (r) {
      if (r.status === 401 && session) {
        save(null);
        show("Your session ended. Sign in again.");
      }
      return r;
    });
  }

  function media(url) {
    if (!url || !session) return url || "";
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(session.token);
  }

  function signOut() {
    var s = session;
    save(null);
    if (s) {
      fetch("/api/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + s.token } })
        .catch(function () {});
    }
    location.reload();
  }

  // ---- the form -------------------------------------------------------
  var css =
    "#ra-auth{position:fixed;inset:0;z-index:50;background:rgba(20,16,12,.55);display:flex;" +
    "align-items:center;justify-content:center;padding:16px;font-family:system-ui,Arial,sans-serif}" +
    "#ra-auth form{background:#fff;color:#222;border-radius:14px;padding:22px;width:100%;max-width:360px;" +
    "text-align:left;box-shadow:0 12px 40px rgba(0,0,0,.25)}" +
    "#ra-auth h2{margin:0 0 4px;font-size:22px}#ra-auth p{margin:0 0 12px;color:#555;font-size:14px}" +
    "#ra-auth label{display:block;font-size:14px;margin:10px 0 4px}" +
    "#ra-auth input{width:100%;box-sizing:border-box;padding:10px;font-size:16px;border:1px solid #bbb;border-radius:8px}" +
    "#ra-auth button{width:100%;margin:14px 0 0;padding:12px;font-size:16px;border:0;border-radius:8px;" +
    "background:#0c6b58;color:#fff;cursor:pointer}#ra-auth .ra-switch{background:none;color:#0c6b58;padding:6px}" +
    "#ra-auth .ra-err{color:#8d3030;font-size:14px;min-height:1em;margin-top:8px}" +
    "#ra-user{font-size:14px;margin:0 0 12px}#ra-user button{margin-left:8px;padding:4px 10px;font-size:14px}";

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text) e.textContent = text;
    return e;
  }

  function field(form, id, label, type, auto) {
    form.appendChild(el("label", { for: id }, label));
    var input = el("input", { id: id, name: id, type: type, autocomplete: auto, required: "" });
    form.appendChild(input);
    return input;
  }

  function show(message) {
    var old = document.getElementById("ra-auth");
    if (old) old.remove();
    if (!document.getElementById("ra-auth-css")) {
      var st = el("style", { id: "ra-auth-css" });
      st.textContent = css;
      document.head.appendChild(st);
    }
    var mode = "login";
    var wrap = el("div", { id: "ra-auth", role: "dialog", "aria-modal": "true", "aria-labelledby": "ra-title" });
    var form = el("form", { novalidate: "" });
    var title = el("h2", { id: "ra-title" });
    var sub = el("p");
    form.appendChild(title);
    form.appendChild(sub);
    var user = field(form, "ra-username", "Username", "text", "username");
    var nameLabel, name, confirmLabel, confirm;
    var pass = field(form, "ra-password", "Password", "password", "current-password");
    var err = el("div", { class: "ra-err", role: "alert", "aria-live": "assertive" }, message || "");
    var submit = el("button", { type: "submit" });
    var swap = el("button", { type: "button", class: "ra-switch" });
    form.appendChild(err);
    form.appendChild(submit);
    form.appendChild(swap);
    wrap.appendChild(form);
    document.body.appendChild(wrap);

    function render() {
      var reg = mode === "register";
      title.textContent = reg ? "Create your RecallAI account" : "Sign in to RecallAI";
      sub.textContent = reg ? "Your belongings, sightings and alerts stay private to your account."
        : "Sign in to see your belongings and share your phone's location.";
      submit.textContent = reg ? "Create account" : "Sign in";
      swap.textContent = reg ? "I already have an account" : "Create an account";
      pass.setAttribute("autocomplete", reg ? "new-password" : "current-password");
      if (reg && !name) {
        nameLabel = el("label", { for: "ra-display" }, "Display name");
        name = el("input", { id: "ra-display", type: "text", autocomplete: "name", maxlength: "64" });
        form.insertBefore(nameLabel, pass.previousSibling);
        form.insertBefore(name, pass.previousSibling);
        confirmLabel = el("label", { for: "ra-confirm" }, "Confirm password");
        confirm = el("input", { id: "ra-confirm", type: "password", autocomplete: "new-password", required: "" });
        form.insertBefore(confirmLabel, err);
        form.insertBefore(confirm, err);
      } else if (!reg && name) {
        [nameLabel, name, confirmLabel, confirm].forEach(function (x) { x.remove(); });
        name = confirm = null;
      }
    }
    render();
    user.focus();
    swap.onclick = function () {
      mode = mode === "login" ? "register" : "login";
      err.textContent = "";
      render();
    };
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var u = user.value.trim().toLowerCase();
      var body = { username: u, password: pass.value };
      if (mode === "register") {
        if (!USERNAME.test(u)) {
          err.textContent = "Username must be 3-32 characters: lowercase letters, numbers, . _ -";
          return;
        }
        if (pass.value.length < 8) { err.textContent = "Password must be at least 8 characters."; return; }
        if (pass.value !== confirm.value) { err.textContent = "Passwords do not match."; return; }
        body.display_name = name.value.trim();
      } else if (!u || !pass.value) {
        err.textContent = "Enter your username and password.";
        return;
      }
      submit.disabled = true;
      err.textContent = "";
      fetch(mode === "register" ? "/api/auth/register" : "/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      }).then(function (r) {
        return r.json().catch(function () { return null; }).then(function (data) {
          if (!r.ok) throw new Error(detail(data, "Sign-in failed (" + r.status + ")"));
          save({ token: data.token, user: data.user, expires_at: data.expires_at });
          wrap.remove();
          start(data.user);
        });
      }).catch(function (e) {
        err.textContent = e.message || "Cannot reach the RecallAI server.";
      }).then(function () { submit.disabled = false; });
    };
  }

  var started = false;
  function start(u) {
    var bar = document.getElementById("ra-user");
    if (!bar) {
      bar = el("p", { id: "ra-user" });
      document.body.insertBefore(bar, document.body.firstChild);
    }
    bar.textContent = "Signed in as " + (u.display_name || u.username) + " (" + u.username + ")";
    var out = el("button", { type: "button" }, "Sign out");
    out.onclick = signOut;
    bar.appendChild(out);
    if (!started && ready) {
      started = true;
      ready(u);
    }
  }

  function require(onReady) {
    ready = onReady;
    if (!session) return show();
    authFetch("/api/auth/me").then(function (r) {
      if (r.ok) return r.json().then(start);
      if (r.status !== 401) throw new Error();
    }).catch(function () { show("Cannot reach the RecallAI server."); });
  }

  window.RecallAuth = {
    require: require,
    fetch: authFetch,
    media: media,
    signOut: signOut,
    token: function () { return session && session.token; },
  };
})();

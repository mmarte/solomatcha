// Click-to-load video: nothing is requested from YouTube until the reader clicks play.
document.addEventListener("click", function (e) {
  var a = e.target.closest && e.target.closest(".video .play");
  if (!a) return;
  var box = a.closest(".video");
  var id = box && box.getAttribute("data-yt");
  if (!id || !/^[A-Za-z0-9_-]{6,20}$/.test(id)) return;
  e.preventDefault();
  var f = document.createElement("iframe");
  f.src = "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=1&rel=0";
  f.title = a.getAttribute("data-title") || "Video";
  f.allow = "accelerometer; autoplay; encrypted-media; picture-in-picture; fullscreen";
  f.allowFullscreen = true;
  a.replaceWith(f);
});

// Cookie consent: analytics scripts (listed in #consent-config) load only after the visitor accepts.
(function () {
  var KEY = "sm_consent";
  function read() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function write(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  var cfg = document.getElementById("consent-config");
  var banner = document.getElementById("consent-banner");
  if (!cfg || !banner) return;
  var loaded = false;
  function loadAnalytics() {
    if (loaded) return;
    loaded = true;
    var list = [];
    try { list = JSON.parse(cfg.textContent); } catch (e) {}
    list.forEach(function (item) {
      var s = document.createElement("script");
      if (item.src) { s.src = item.src; s.async = true; } else { s.text = item.code; }
      document.head.appendChild(s);
    });
  }
  function clearCookies() {
    var host = location.hostname.replace(/^www\./, "");
    document.cookie.split(";").forEach(function (c) {
      var name = c.split("=")[0].trim();
      if (/^(_ga|_gid|_gat)/.test(name)) {
        ["", "; domain=" + host, "; domain=." + host].forEach(function (d) {
          document.cookie = name + "=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/" + d;
        });
      }
    });
  }
  function show() { banner.hidden = false; }
  function hide() { banner.hidden = true; }
  function choose(v) {
    var before = read();
    write(v);
    hide();
    if (v === "granted") loadAnalytics();
    else { clearCookies(); if (before === "granted") location.reload(); }
  }
  var saved = read();
  if (saved === "granted") loadAnalytics();
  else if (saved !== "denied") show();
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-consent],[data-consent-open]");
    if (!t) return;
    if (t.hasAttribute("data-consent-open")) { show(); return; }
    choose(t.getAttribute("data-consent"));
  });
})();

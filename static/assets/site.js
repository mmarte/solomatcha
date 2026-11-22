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

// Price calculator (Tools page)
(function () {
  var price = document.getElementById("c-price");
  if (!price) return;
  var grams = document.getElementById("c-grams"), cup = document.getElementById("c-cup");
  function fmt(n) { return isFinite(n) && n > 0 ? n.toLocaleString(undefined, { maximumFractionDigits: n < 1 ? 3 : 2 }) : "–"; }
  function calc() {
    var p = parseFloat(price.value), g = parseFloat(grams.value), c = parseFloat(cup.value);
    var ppg = p / g;
    document.getElementById("c-ppg").textContent = fmt(ppg);
    document.getElementById("c-ppc").textContent = fmt(ppg * c);
    document.getElementById("c-cups").textContent = fmt(Math.floor(g / c));
  }
  [price, grams, cup].forEach(function (el) { el.addEventListener("input", calc); });
  calc();
})();

// Site search: filters /search.json in the browser; nothing is sent anywhere.
(function () {
  var box = document.getElementById("results");
  var input = document.getElementById("q");
  if (!box || !input) return;
  var data = null;
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function render() {
    var q = input.value.trim().toLowerCase();
    if (!data || !q) { box.innerHTML = q ? "" : '<li class="empty">Type to search guides, products and tools.</li>'; return; }
    var words = q.split(/\s+/);
    var hits = data.map(function (r) {
      var hay = (r.t + " " + r.d).toLowerCase(), score = 0;
      for (var i = 0; i < words.length; i++) {
        if (hay.indexOf(words[i]) === -1) return null;
        score += r.t.toLowerCase().indexOf(words[i]) !== -1 ? 2 : 1;
      }
      return { r: r, s: score };
    }).filter(Boolean).sort(function (a, b) { return b.s - a.s; });
    box.innerHTML = hits.length ? hits.map(function (h) {
      return '<li><span class="t"><a href="' + esc(h.r.u) + '">' + esc(h.r.t) + '</a></span><span class="k">' + esc(h.r.k) + '</span><p class="d">' + esc(h.r.d) + '</p></li>';
    }).join("") : '<li class="empty">No results. Try a shorter word.</li>';
  }
  var qs = new URLSearchParams(location.search).get("q");
  if (qs) input.value = qs;
  input.addEventListener("input", render);
  fetch("/search.json").then(function (r) { return r.json(); }).then(function (d) { data = d; render(); }).catch(function () {
    box.innerHTML = '<li class="empty">Search could not load. Browse the <a href="/guides/">archive</a> instead.</li>';
  });
})();

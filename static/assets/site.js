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

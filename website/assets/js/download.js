/* Samiksha Technologies — download page enhancement.
   The page already renders a complete, correct download box with no
   JavaScript at all (see download.html): a real version, size and date, and a
   working link. This script only asks the backend if there is something more
   current to show, and if so, updates those same elements in place. Any
   failure -- offline, CORS not yet configured, nothing published -- leaves
   the static content exactly as it was. Same philosophy as main.js's contact
   form: a missing backend must never be a broken page. */

(function () {
  "use strict";

  // Set this to the backend's real HTTPS origin once it's deployed (see
  // backend/README.md for how it's hosted). Until it's replaced, this fetch
  // fails harmlessly and the static content below is what visitors see --
  // which is the safe default, not a bug.
  var API_BASE = "https://photoflow-api.onrender.com/api/v1";

  var box = document.querySelector("[data-release-box]");
  if (!box || API_BASE.indexOf("REPLACE-WITH-YOUR-BACKEND-URL") !== -1) {
    return;
  }

  var params = new URLSearchParams({
    product: "photoflow",
    platform: "Windows",
    channel: "stable"
  });

  fetch(API_BASE + "/releases/current?" + params.toString(), {
    headers: { Accept: "application/json" }
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("no published release");
      }
      return response.json();
    })
    .then(function (release) {
      if (!release.download_url) {
        return;
      }

      var link = box.querySelector("[data-release-link]");
      var badgeText = box.querySelector("[data-release-badge-text]");
      var meta = box.querySelector("[data-release-meta]");

      if (link) {
        link.setAttribute("href", release.download_url);
        link.textContent = "Download PhotoFlow " + release.version;
      }

      if (badgeText) {
        badgeText.textContent =
          (release.channel === "stable" ? "Current" : release.channel) +
          " " + release.version;
      }

      if (meta) {
        var parts = [(release.platform || "Windows") + " (64-bit)"];

        if (release.size_bytes) {
          parts.push(
            "Approx. " + Math.round(release.size_bytes / (1024 * 1024)) + " MB"
          );
        }

        if (release.published_at) {
          var released = new Date(release.published_at);
          parts.push(
            "Released " +
            released.toLocaleDateString(undefined, {
              day: "numeric",
              month: "long",
              year: "numeric"
            })
          );
        }

        meta.innerHTML = parts
          .map(function (part) {
            return "<span>" + part + "</span>";
          })
          .join("");
      }
    })
    .catch(function () {
      // Backend unreachable, CORS not configured, or nothing published yet --
      // the static fallback already in the page is exactly right. Nothing to do.
    });
})();

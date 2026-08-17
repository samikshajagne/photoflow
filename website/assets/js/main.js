/* SA Innovations — site behaviour.
   Deliberately tiny and dependency-free: the mobile nav toggle, marking the
   current page in the nav, and the contact form's mailto fallback. The site
   works fine with JavaScript disabled; this only adds convenience. */

(function () {
  "use strict";

  /* ---------------------------------------------------------------- Mobile nav */
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.querySelector(".site-nav");

  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    // Close the menu after following a link on mobile.
    nav.addEventListener("click", function (event) {
      if (event.target.tagName === "A") {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  /* ------------------------------------------------- Highlight the current page */
  // Pages ship without hardcoding "active" on every nav item, so it's derived
  // from the URL instead -- one less thing to forget when adding a page.
  var here = window.location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".site-nav a").forEach(function (link) {
    var target = link.getAttribute("href");
    if (target === here) {
      link.classList.add("active");
      link.setAttribute("aria-current", "page");
    }
  });

  /* ------------------------------------------------------------- Contact form */
  // No backend yet, so the form composes an email instead of silently failing.
  var form = document.querySelector("[data-mailto-form]");
  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var to = form.getAttribute("data-mailto-form");
      var name = (form.querySelector("[name=name]") || {}).value || "";
      var email = (form.querySelector("[name=email]") || {}).value || "";
      var studio = (form.querySelector("[name=studio]") || {}).value || "";
      var topic = (form.querySelector("[name=topic]") || {}).value || "General";
      var message = (form.querySelector("[name=message]") || {}).value || "";

      var body =
        "Name: " + name + "\n" +
        "Email: " + email + "\n" +
        "Studio: " + studio + "\n\n" +
        message;

      window.location.href =
        "mailto:" + to +
        "?subject=" + encodeURIComponent("[" + topic + "] Website enquiry") +
        "&body=" + encodeURIComponent(body);
    });
  }

  /* ------------------------------------------------------------- Footer year */
  document.querySelectorAll("[data-year]").forEach(function (node) {
    node.textContent = String(new Date().getFullYear());
  });

  /* ------------------------------------------------------------ Scroll reveal */
  // Progressive enhancement: the "reveal-ready" class is only added when we can
  // actually animate, so with JS off (or without IntersectionObserver) the
  // content simply stays visible instead of being hidden forever.
  var revealables = document.querySelectorAll(".reveal");
  if (revealables.length && "IntersectionObserver" in window) {
    var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!reduced) {
      document.documentElement.classList.add("reveal-ready");
      var observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("in");
              observer.unobserve(entry.target);
            }
          });
        },
        { rootMargin: "0px 0px -8% 0px", threshold: 0.06 }
      );
      revealables.forEach(function (node, index) {
        // Small stagger so grids cascade rather than snapping in together.
        node.style.transitionDelay = (index % 4) * 70 + "ms";
        observer.observe(node);
      });
    }
  }
})();

// Amélioration progressive : le site fonctionne entièrement sans JavaScript.
(function () {
  "use strict";

  // Bascule de thème : respecte le réglage système tant que la personne n'a rien choisi.
  var toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    var root = document.documentElement;
    var media = window.matchMedia("(prefers-color-scheme: dark)");
    var label = toggle.querySelector(".visually-hidden");

    var isDark = function () {
      var t = root.dataset.theme;
      return t ? t === "dark" : media.matches;
    };
    var sync = function () {
      var dark = isDark();
      toggle.setAttribute("aria-pressed", String(dark));
      if (label) label.textContent = "Thème sombre";
      toggle.title = dark ? "Passer au thème clair" : "Passer au thème sombre";
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.content = dark ? "#0e1014" : "#f6f3ec";
    };

    toggle.hidden = false;
    toggle.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) {}
      sync();
    });
    media.addEventListener("change", sync);
    sync();
  }

  // Onglets de l'accueil (motif ARIA « tabs ») : sans JS, ce sont de simples ancres et tout s'affiche.
  var tabs = document.querySelector("[data-tabs]");
  if (tabs) {
    var list = tabs.querySelector("[data-tablist]");
    var links = Array.prototype.slice.call(tabs.querySelectorAll("[data-tab]"));
    var panels = links.map(function (a) { return document.getElementById(a.hash.slice(1)); });

    list.setAttribute("role", "tablist");
    list.setAttribute("aria-label", "Sections du QG");
    tabs.querySelector(".tablist-wrap").removeAttribute("aria-label");
    links.forEach(function (a, i) {
      a.parentElement.setAttribute("role", "presentation");
      a.setAttribute("role", "tab");
      a.id = "tab-" + panels[i].id;
      a.setAttribute("aria-controls", panels[i].id);
      panels[i].setAttribute("role", "tabpanel");
      panels[i].setAttribute("aria-labelledby", a.id);
      panels[i].setAttribute("tabindex", "-1");
    });
    tabs.classList.add("is-enhanced");

    var select = function (index, focus, push) {
      links.forEach(function (a, i) {
        var on = i === index;
        a.setAttribute("aria-selected", String(on));
        a.tabIndex = on ? 0 : -1;
        panels[i].hidden = !on;
      });
      if (focus) links[index].focus();
      if (push) history.replaceState(null, "", links[index].hash);
    };
    var fromHash = function () {
      var i = links.findIndex(function (a) { return a.hash === location.hash; });
      return i < 0 ? 0 : i;
    };

    links.forEach(function (a, i) {
      a.addEventListener("click", function (ev) { ev.preventDefault(); select(i, false, true); });
      a.addEventListener("keydown", function (ev) {
        var next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: links.length - 1 }[ev.key];
        if (next === undefined) return;
        ev.preventDefault();
        select((next + links.length) % links.length, true, true);
      });
    });
    window.addEventListener("hashchange", function () { select(fromHash(), false, false); });
    select(fromHash(), false, false);
    // Arrivée sur un onglet précis (#squad après un formulaire) : on amène les onglets à l'écran.
    if (location.hash && fromHash() > 0) tabs.scrollIntoView({ block: "start" });
  }
  // Après un formulaire en erreur, on amène le champ fautif à l'écran (le focus est déjà posé par autofocus).
  var invalid = document.querySelector('[aria-invalid="true"]');
  if (invalid) {
    invalid.focus();
    invalid.scrollIntoView({ block: "center" });
  }
})();

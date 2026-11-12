// Amélioration progressive : le site fonctionne entièrement sans JavaScript.
(function () {
  "use strict";

  document.querySelectorAll("[data-chart-picker]").forEach(function (picker) {
    var control = picker.querySelector("[data-chart-control]");
    var select = picker.querySelector("[data-chart-select]");
    if (!control || !select) return;
    var panels = picker.querySelectorAll("[data-chart-panel]");
    function updateChart() {
      panels.forEach(function (panel) {
        panel.hidden = panel.dataset.chartPanel !== select.value;
      });
    }
    select.addEventListener("change", updateChart);
    updateChart();
    control.hidden = false;
  });

  document.querySelectorAll("[data-instance-form]").forEach(function (form) {
    var picker = form.querySelector("[data-instance-picker]");
    var select = form.elements.namedItem("instance_key");
    var preview = form.querySelector("[data-instance-preview]");
    var kind = form.elements.namedItem("type_objectif");
    function showGuide() {
      var template = document.getElementById("instance-guide-" + select.value);
      preview.replaceChildren();
      preview.hidden = !template;
      if (template) preview.appendChild(template.content.cloneNode(true));
    }
    select.addEventListener("change", function () {
      var option = select.options[select.selectedIndex];
      if (option.value) {
        form.elements.namedItem("titre").value = option.dataset.title;
        kind.value = option.dataset.kind;
        form.elements.namedItem("niveau").value = option.dataset.level;
        form.elements.namedItem("effectif").value = option.dataset.size;
      }
      showGuide();
    });
    kind.addEventListener("change", function () {
      if (select.value && select.options[select.selectedIndex].dataset.kind !== kind.value) {
        select.value = "";
        showGuide();
      }
    });
    showGuide();
    picker.hidden = false;
  });

  document.querySelectorAll("[data-copy-coordinate]").forEach(function (button) {
    if (!navigator.clipboard) return;
    button.hidden = false;
    button.addEventListener("click", async function () {
      try {
        await navigator.clipboard.writeText(button.dataset.copyCoordinate);
        button.textContent = "Copié";
      } catch (error) {
        button.textContent = "Copie indisponible";
      }
    });
  });

  document.querySelectorAll("[data-world-map]").forEach(function (map) {
    var zone = map.querySelector("#map-zone");
    var search = map.querySelector("#map-search");
    function fold(value) { return value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
    var filters = Array.from(map.querySelectorAll("[data-map-filters] input"));
    var pins = Array.from(map.querySelectorAll("[data-point]"));
    var places = Array.from(map.querySelectorAll("[data-place]"));
    var active = map.dataset.selected;
    var image = map.querySelector("[data-map-image]");
    var unavailable = map.querySelector("[data-map-unavailable]");
    function background() {
      var url = zone.options[zone.selectedIndex].dataset.background;
      unavailable.hidden = Boolean(url);
      image.hidden = !url;
      if (url && image.getAttribute("src") !== url) image.src = url;
      image.alt = "Carte de la zone " + zone.value;
    }
    image.addEventListener("load", function () { unavailable.hidden = true; image.hidden = false; update(); });
    image.addEventListener("error", function () { unavailable.hidden = false; image.hidden = true; update(); });
    function update() {
      var kinds = filters.filter(function (filter) { return filter.checked; }).map(function (filter) { return filter.value; });
      var query = fold(search.value.trim());
      var visible = function (el) {
        return el.dataset.zone === zone.value && kinds.includes(el.dataset.mapKind) && fold(el.dataset.mapName).includes(query);
      };
      pins.forEach(function (pin) {
        pin.hidden = !visible(pin) || image.hidden || !image.complete || !image.naturalWidth;
        pin.setAttribute("aria-pressed", String(!pin.hidden && pin.dataset.point === active));
      });
      places.forEach(function (place) { place.hidden = !visible(place); });
      map.querySelector("[data-map-empty]").hidden = places.some(function (place) { return !place.hidden; });
    }
    pins.forEach(function (pin) {
      pin.addEventListener("click", function () {
        active = pin.dataset.point;
        places.forEach(function (place) { place.open = place.dataset.place === active; });
        var target = places.find(function (place) { return place.dataset.place === active; });
        if (target) {
          target.querySelector("summary").focus({preventScroll: true});
          target.scrollIntoView({block: "nearest"});
        }
        update();
      });
    });
    places.forEach(function (place) {
      place.addEventListener("toggle", function () {
        if (place.open && !place.hidden) { active = place.dataset.place; update(); }
      });
    });
    zone.addEventListener("change", function () { background(); update(); });
    search.addEventListener("input", update);
    filters.forEach(function (filter) { filter.addEventListener("change", update); });
    map.querySelector("[data-map-board]").hidden = false;
    map.querySelector("[data-map-filters]").hidden = false;
    map.querySelector("[data-map-search-wrap]").hidden = false;
    map.querySelector("[data-map-submit]").hidden = true;
    background();
    if (image.complete && !image.naturalWidth) {
      image.hidden = true;
      unavailable.hidden = false;
    }
    update();
  });

  document.querySelectorAll("[data-squad-replay]").forEach(function (replay) {
    var frames = JSON.parse(replay.querySelector("[data-replay-data]").textContent);
    var range = replay.querySelector("input");
    var play = replay.querySelector("[data-replay-play]");
    var date = replay.querySelector("[data-replay-date]");
    var plot = replay.querySelector("[data-replay-plot]");
    var legend = replay.querySelector("[data-replay-legend]");
    var observations = frames.flatMap(function (frame) { return frame.points; });
    var lowX = Math.floor(Math.min(0, ...observations.map(function (p) { return p.level; })));
    var highX = Math.max(lowX + 4, Math.ceil(Math.max(0, ...observations.map(function (p) { return p.level; }))));
    var lowY = Math.floor(Math.min(0, ...observations.map(function (p) { return p.gear; })));
    var highY = Math.max(lowY + 4, Math.ceil(Math.max(0, ...observations.map(function (p) { return p.gear; }))));
    var timer;
    var format = new Intl.NumberFormat("fr-FR", {signDisplay: "exceptZero", maximumFractionDigits: 1});
    var axisFormat = new Intl.NumberFormat("fr-FR", {maximumFractionDigits: 1});
    var ns = "http://www.w3.org/2000/svg";
    function svg(parent, tag, attrs, label) {
      var node = document.createElementNS(ns, tag);
      Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
      if (label !== undefined) node.textContent = label;
      parent.appendChild(node);
      return node;
    }
    function draw() {
      var width = plot.clientWidth;
      if (!width) return;
      var index = Number(range.value);
      var frame = frames[index];
      var x = function (v) { return 54 + (width - 78) * (v - lowX) / (highX - lowX); };
      var y = function (v) { return 224 - 188 * (v - lowY) / (highY - lowY); };
      plot.setAttribute("viewBox", "0 0 " + width + " 270");
      plot.replaceChildren();
      legend.replaceChildren();
      date.textContent = frame.date;
      svg(plot, "text", {x: 43, y: 15}, "Équipement gagné (ilvl)");
      svg(plot, "text", {x: width - 15, y: 263, "text-anchor": "end"}, "Niveaux gagnés");
      for (var tick = 0; tick <= 4; tick += 1) {
        var yValue = lowY + (highY - lowY) * tick / 4;
        var xValue = Math.round(lowX + (highX - lowX) * tick / 4);
        svg(plot, "line", {x1: 43, x2: width - 13, y1: y(yValue), y2: y(yValue)});
        svg(plot, "text", {x: 35, y: y(yValue) + 4, "text-anchor": "end"}, axisFormat.format(yValue));
        svg(plot, "line", {x1: x(xValue), x2: x(xValue), y1: 26, y2: 233});
        svg(plot, "text", {x: x(xValue), y: 249, "text-anchor": "middle"}, xValue);
      }
      frame.points.forEach(function (point) {
        var trail = [point];
        for (var i = index - 1; i >= Math.max(0, index - 3); i -= 1) {
          var previous = frames[i].points.find(function (item) { return item.name === point.name; });
          if (!previous) break;
          trail.unshift(previous);
        }
        if (trail.length > 1) svg(plot, "polyline", {
          points: trail.map(function (p) { return x(p.level) + "," + y(p.gear); }).join(" "),
          stroke: point.color, "stroke-width": 2, opacity: .42, fill: "none"
        });
        var dot = svg(plot, "circle", {cx: x(point.level), cy: y(point.gear), r: 7, fill: point.color});
        var label = point.name + " · " + format.format(point.level) + " niv. · " + format.format(point.gear)
          + " ilvl · " + axisFormat.format(point.days) + " j observés";
        svg(dot, "title", {}, label);
        var item = document.createElement("li");
        var swatch = document.createElement("span");
        swatch.className = "legend-swatch";
        swatch.style.backgroundColor = point.color;
        swatch.setAttribute("aria-hidden", "true");
        item.append(swatch, label);
        legend.appendChild(item);
      });
    }
    function stop() {
      window.clearInterval(timer);
      timer = undefined;
      if (play) play.textContent = "Lire";
    }
    range.addEventListener("input", function () { stop(); draw(); });
    if (play) play.addEventListener("click", function () {
      if (timer !== undefined) return stop();
      if (Number(range.value) === frames.length - 1) range.value = 0;
      play.textContent = "Pause";
      draw();
      timer = window.setInterval(function () {
        range.value = Number(range.value) + 1;
        draw();
        if (Number(range.value) >= frames.length - 1) stop();
      }, 760);
    });
    replay.hidden = false;
    new ResizeObserver(draw).observe(plot);
    draw();
  });

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
  // Cadrage de la photo : aperçu identique au recadrage serveur (photos.crop_box), glisser ou curseurs.
  var crop = document.querySelector("[data-crop]");
  var photoInput = document.querySelector("[data-photo-input]");
  if (crop && photoInput) {
    var stage = crop.querySelector("[data-crop-stage]");
    var zoom = crop.querySelector("[data-crop-zoom]");
    var posX = crop.querySelector("[data-crop-x]");
    var posY = crop.querySelector("[data-crop-y]");
    var natural = null;
    crop.hidden = true;  // s'affiche une fois l'image choisie (sans JS, les curseurs restent utilisables)

    var render = function () {
      if (!natural) return;
      var w = natural.w, h = natural.h, z = parseFloat(zoom.value);
      var side = Math.min(w, h) / z;
      var left = Math.min(Math.max(posX.value / 100 * w - side / 2, 0), w - side);
      var top = Math.min(Math.max(posY.value / 100 * h - side / 2, 0), h - side);
      var px = stage.clientWidth / side;
      stage.style.backgroundSize = (w * px) + "px " + (h * px) + "px";
      stage.style.backgroundPosition = (-left * px) + "px " + (-top * px) + "px";
      zoom.setAttribute("aria-valuetext", "Zoom " + z.toFixed(1).replace(".", ",") + " fois");
      posX.setAttribute("aria-valuetext", posX.value + " %");
      posY.setAttribute("aria-valuetext", posY.value + " %");
    };

    photoInput.addEventListener("change", function () {
      var file = photoInput.files && photoInput.files[0];
      if (!file || !/^image\//.test(file.type)) { crop.hidden = true; return; }
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        natural = { w: img.naturalWidth, h: img.naturalHeight };
        zoom.value = 1; posX.value = 50; posY.value = 50;
        stage.style.backgroundImage = "url(" + url + ")";
        stage.textContent = "";
        crop.hidden = false;
        render();
      };
      img.src = url;
    });
    [zoom, posX, posY].forEach(function (input) { input.addEventListener("input", render); });

    // Glisser l'image : on déplace le centre du cadrage, en sens inverse du geste.
    var drag = null;
    stage.addEventListener("pointerdown", function (ev) {
      if (!natural) return;
      drag = { x: ev.clientX, y: ev.clientY, px: +posX.value, py: +posY.value };
      stage.setPointerCapture(ev.pointerId);
    });
    stage.addEventListener("pointermove", function (ev) {
      if (!drag) return;
      var side = Math.min(natural.w, natural.h) / parseFloat(zoom.value);
      var scale = side / stage.clientWidth;  // pixels image par pixel écran
      posX.value = Math.min(100, Math.max(0, drag.px - (ev.clientX - drag.x) * scale / natural.w * 100));
      posY.value = Math.min(100, Math.max(0, drag.py - (ev.clientY - drag.y) * scale / natural.h * 100));
      render();
    });
    ["pointerup", "pointercancel"].forEach(function (t) { stage.addEventListener(t, function () { drag = null; }); });
    stage.addEventListener("wheel", function (ev) {
      if (!natural) return;
      ev.preventDefault();
      zoom.value = Math.min(3, Math.max(1, parseFloat(zoom.value) - ev.deltaY * 0.002));
      render();
    }, { passive: false });
  }
  // Après un formulaire en erreur, on amène le champ fautif à l'écran (le focus est déjà posé par autofocus).
  var invalid = document.querySelector('[aria-invalid="true"]');
  if (invalid) {
    invalid.focus();
    invalid.scrollIntoView({ block: "center" });
  }
})();

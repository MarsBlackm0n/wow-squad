// Amélioration progressive : le site fonctionne entièrement sans JavaScript.
(function () {
  "use strict";

  function chartPlayers(root, colors, redraw) {
    var control = root.querySelector("[data-chart-players]");
    var active = control.querySelector("[data-chart-active]");
    var clear = control.querySelector("[data-chart-clear]");
    var search = control.querySelector("[data-chart-search]");
    var groups = Array.from(control.querySelectorAll("[data-chart-player]"));
    var selected = new Set();
    function fold(value) {
      return value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
    }
    function filter() {
      var query = fold(search.value.trim());
      groups.forEach(function (group) {
        var matchesPlayer = fold(group.dataset.playerName).includes(query);
        var options = Array.from(group.querySelectorAll("[data-chart-option]"));
        options.forEach(function (option) {
          option.hidden = !matchesPlayer && !fold(option.dataset.characterName).includes(query);
        });
        group.hidden = options.every(function (option) { return option.hidden; });
        if (query && !group.hidden) group.open = true;
      });
      control.querySelector("[data-chart-no-results]").hidden = groups.some(function (group) { return !group.hidden; });
    }
    function update() {
      var focused = active.contains(document.activeElement) ? document.activeElement.dataset.id : null;
      selected.clear();
      active.replaceChildren();
      groups.forEach(function (group) {
        var inputs = Array.from(group.querySelectorAll("input"));
        var checked = inputs.filter(function (input) { return input.checked; });
        group.querySelector("[data-chart-count]").textContent = checked.length + "/" + inputs.length;
        checked.forEach(function (input) {
          selected.add(input.value);
          var button = document.createElement("button");
          button.type = "button";
          button.dataset.id = input.value;
          button.className = input.parentElement.className;
          button.style.setProperty("--series-color", colors.get(input.value));
          button.append(input.parentElement.querySelector(".legend-swatch").cloneNode(true),
                        input.parentElement.querySelector("[data-chart-character]").cloneNode(true), " ×");
          button.setAttribute("aria-label", "Masquer " + input.parentElement.dataset.characterName);
          button.addEventListener("click", function () { input.checked = false; update(); });
          active.appendChild(button);
        });
      });
      clear.hidden = !selected.size;
      if (!selected.size) active.textContent = "Aucun personnage s\u00e9lectionn\u00e9";
      if (focused) (Array.from(active.children).find(function (button) { return button.dataset.id === focused; })
                   || active.firstElementChild || control.querySelector(".chart-player-picker > summary")).focus();
      redraw(selected);
    }
    control.querySelectorAll("[data-chart-option] input").forEach(function (input) {
      input.parentElement.style.setProperty("--series-color", colors.get(input.value));
      input.addEventListener("change", update);
    });
    clear.addEventListener("click", function () {
      control.querySelectorAll("[data-chart-option] input").forEach(function (input) { input.checked = false; });
      update();
      control.querySelector(".chart-player-picker > summary").focus();
    });
    search.addEventListener("input", filter);
    control.hidden = !groups.length;
    return {selected: selected, update: update};
  }

  document.querySelectorAll('[data-chart-panel="comparison"]').forEach(function (figure) {
    var fallback = figure.querySelector("[data-race-fallback]");
    var colors = new Map(Array.from(fallback.children, function (item) {
      return [item.dataset.raceId, item.dataset.color];
    }));
    var groups = figure.querySelectorAll("[data-race-series]");
    var empty = figure.querySelector("[data-race-empty]");
    chartPlayers(figure, colors, function (selected) {
      groups.forEach(function (group) {
        group.setAttribute("display", selected.has(group.dataset.raceSeries) ? "inline" : "none");
      });
      empty.hidden = selected.size > 0;
    }).update();
    fallback.hidden = true;
  });

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

  document.querySelectorAll("[data-copy-coordinate], [data-copy-link]").forEach(function (button) {
    if (!navigator.clipboard) return;
    button.hidden = false;
    button.addEventListener("click", async function () {
      try {
        var value = button.dataset.copyLink
          ? new URL(button.dataset.copyLink, window.location.href).href
          : button.dataset.copyCoordinate;
        await navigator.clipboard.writeText(value);
        button.textContent = "Copié";
      } catch (error) {
        button.textContent = "Copie indisponible";
      }
    });
  });

  document.querySelectorAll("[data-world-map]").forEach(function (map) {
    var zone = map.querySelector("#map-zone");
    var search = map.querySelector("#map-search");
    function fold(value) {
      return value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[\u2019'-]/g, " ");
    }
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
      var query = fold(search.value).trim();
      var words = query ? query.split(/\s+/) : [];
      var matches = function (el) {
        var kindsMatch = el.dataset.mapKinds.split(" ").some(function (kind) { return kinds.includes(kind); });
        var name = fold(el.dataset.mapName);
        return kindsMatch && words.every(function (word) { return name.includes(word); });
      };
      pins.forEach(function (pin) {
        pin.hidden = pin.dataset.zone !== zone.value || !matches(pin) || image.hidden || !image.complete || !image.naturalWidth;
        pin.setAttribute("aria-pressed", String(!pin.hidden && pin.dataset.point === active));
      });
      places.forEach(function (place) {
        place.hidden = (!query && place.dataset.zone !== zone.value) || !matches(place);
        place.querySelector("[data-place-zone]").hidden = !query;
      });
      map.querySelector("[data-map-places-title]").textContent = query ? "Résultats dans toutes les zones" : "Repères de la zone";
      var mobStatus = map.querySelector("[data-map-mobs]");
      var count = Number(zone.options[zone.selectedIndex].dataset.mobs);
      mobStatus.hidden = Boolean(query) || !kinds.includes("mob");
      mobStatus.textContent = count ? count + (count === 1 ? " mob recensé dans cette zone." : " mobs recensés dans cette zone.")
        : "Aucun mob recensé dans cette zone. Les zones avec des repères de mobs sont signalées dans le sélecteur.";
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
        if (!place.open || place.hidden) return;
        active = place.dataset.place;
        places.forEach(function (other) { if (other !== place) other.open = false; });
        if (zone.value !== place.dataset.zone) {
          zone.value = place.dataset.zone;
          background();
        }
        update();
      });
    });
    zone.addEventListener("change", function () { background(); update(); });
    search.addEventListener("input", update);
    search.addEventListener("keydown", function (event) {
      if (event.key === "Enter") event.preventDefault();
    });
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

  document.querySelectorAll("[data-player-filter]").forEach(function (filter) {
    var all = filter.querySelector("[data-player-all]");
    var toggles = Array.from(filter.querySelectorAll("[data-player-toggle]"));
    var cards = filter.closest(".squad-members").querySelectorAll("[data-squad-player]");
    var empty = filter.querySelector("[data-player-empty]");
    function update() {
      var visible = new Set(toggles.filter(function (toggle) { return toggle.checked; })
        .map(function (toggle) { return toggle.value; }));
      cards.forEach(function (card) { card.hidden = !visible.has(card.dataset.squadPlayer); });
      all.checked = visible.size === toggles.length;
      all.indeterminate = visible.size > 0 && !all.checked;
      empty.hidden = visible.size > 0;
    }
    all.addEventListener("change", function () {
      toggles.forEach(function (toggle) { toggle.checked = all.checked; });
      update();
    });
    toggles.forEach(function (toggle) { toggle.addEventListener("change", update); });
    filter.hidden = false;
    update();
  });

  document.querySelectorAll("[data-profession-radar]").forEach(function (radar) {
    var series = JSON.parse(radar.querySelector("[data-radar-data]").textContent);
    var note = radar.querySelector("[data-radar-note]");
    var plot = radar.querySelector("[data-radar-plot]");
    var tableWrap = radar.querySelector("[data-radar-table]");
    var colors = ["var(--teal)", "var(--violet)", "var(--gold)", "#df6384", "#5d9de8", "#85ad49"];
    var modes = radar.querySelectorAll("[data-radar-mode]");
    var mode = "activity";
    var axes = [];
    modes.forEach(function (button) {
      button.addEventListener("click", function () {
        mode = button.dataset.radarMode;
        modes.forEach(function (item) {
          item.setAttribute("aria-pressed", String(item === button));
        });
        draw();
      });
    });
    var picker = chartPlayers(radar, new Map(series.map(function (row, i) {
      return [String(row.id), colors[i % colors.length]];
    })), draw);
    plot.setAttribute("viewBox", "0 0 620 485");
    radar.hidden = false;
    radar.querySelector("details").hidden = !series.length;
    function shape(tag, attrs, text) {
      var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      Object.entries(attrs).forEach(function (pair) { node.setAttribute(pair[0], pair[1]); });
      if (text !== undefined) node.textContent = text;
      plot.appendChild(node);
    }
    function point(index, radius) {
      var angle = index * 2 * Math.PI / axes.length - Math.PI / 2;
      return [310 + Math.cos(angle) * radius, 235 + Math.sin(angle) * radius];
    }
    function draw() {
      var group = mode;
      var isSkills = group === "skills";
      axes = Array.from(new Set(series.flatMap(function (row) { return Object.keys(row[group] || {}); })));
      if (isSkills) axes.sort(function (a, b) { return a.localeCompare(b, "fr"); });
      var limits = axes.map(function (axis) {
        var values = series.flatMap(function (row) { return isSkills ? Object.values(row.skills || {}) : [row.activity[axis] ?? 0]; });
        return Math.max(isSkills ? 300 : axis === "Niveau" ? 60 : 1, ...values);
      });
      function valueFor(row, axis) {
        return isSkills && row.skills ? row.skills[axis] ?? 0 : row[group]?.[axis];
      }
      var selected = series.map(function (row, i) {
        return {row: row, color: colors[i % colors.length], checked: picker.selected.has(String(row.id))};
      }).filter(function (item) { return item.checked; });
      plot.replaceChildren();
      tableWrap.replaceChildren();
      var showPlot = axes.length >= 3 && selected.length > 0;
      plot.toggleAttribute("hidden", !showPlot);
      note.textContent = !series.length ? "Aucun personnage suivi pour ce groupe."
        : !selected.length ? "Sélectionne au moins un personnage."
        : axes.length < 3 ? "Le radar apparaît à partir de trois métiers relevés. Les niveaux restent dans le tableau."
        : isSkills ? "Les axes de métier partagent une échelle de 0 à " + limits[0] + ". Les métiers absents d’une liste reçue valent 0."
        : "";
      note.hidden = !note.textContent;
      if (!selected.length) return;
      var table = document.createElement("table");
      var header = table.createTHead().insertRow();
      [isSkills ? "Métier" : "Indicateur", "Bord du radar"].concat(selected.map(function (item) { return item.row.name; })).forEach(function (name) {
        var th = document.createElement("th");
        th.scope = "col";
        th.textContent = name;
        header.appendChild(th);
      });
      var body = table.createTBody();
      axes.forEach(function (axis, index) {
        var row = body.insertRow();
        var th = document.createElement("th");
        th.scope = "row";
        th.textContent = axis;
        row.appendChild(th);
        row.insertCell().textContent = limits[index];
        selected.forEach(function (item) {
          row.insertCell().textContent = valueFor(item.row, axis) ?? "Non relevé";
        });
      });
      tableWrap.appendChild(table);
      if (!showPlot) return;
      [35, 70, 105, 140, 175].forEach(function (radius) {
        shape("polygon", {points: axes.map(function (_, i) { return point(i, radius).join(","); }).join(" "),
                          fill: "none", stroke: "var(--line)"});
      });
      axes.forEach(function (axis, i) {
        var edge = point(i, 175);
        var label = point(i, 195);
        shape("line", {x1: 310, y1: 235, x2: edge[0], y2: edge[1], stroke: "var(--line)"});
        shape("text", {x: label[0], y: label[1], "text-anchor": "middle", "dominant-baseline": "middle",
                       "font-size": 15, fill: "var(--text)"}, axis);
      });
      selected.forEach(function (item) {
        var dots = axes.map(function (axis, i) {
          var value = valueFor(item.row, axis);
          return typeof value === "number" ? point(i, value / limits[i] * 175) : null;
        });
        var known = dots.filter(Boolean);
        var coordinates = known.map(function (dot) { return dot.join(","); }).join(" ");
        if (known.length >= 3) {
          shape("polygon", {points: coordinates, fill: item.color, "fill-opacity": .23,
                            stroke: item.color, "stroke-width": 2.4, "stroke-linejoin": "round"});
        } else if (known.length === 2) {
          shape("polyline", {points: coordinates, fill: "none", stroke: item.color, "stroke-width": 2.4});
        }
        known.forEach(function (dot) {
          shape("circle", {cx: dot[0], cy: dot[1], r: 4.5, fill: item.color});
        });
      });
    }
    picker.update();
  });

  document.querySelectorAll("[data-squad-replay]").forEach(function (replay) {
    var frames = JSON.parse(replay.querySelector("[data-replay-data]").textContent);
    var range = replay.querySelector("input[type=range]");
    var play = replay.querySelector("[data-replay-play]");
    var date = replay.querySelector("[data-replay-date]");
    var plot = replay.querySelector("[data-replay-plot]");
    var legend = replay.querySelector("[data-replay-legend]");
    var observations = frames.flatMap(function (frame) { return frame.points; });
    var empty = replay.querySelector("[data-replay-empty]");
    var colors = new Map(observations.map(function (point) { return [String(point.id), point.color]; }));
    var picker = chartPlayers(replay, colors, draw);
    var highX = Math.max(2, Math.ceil(Math.max(...observations.map(function (p) { return Math.abs(p.level); }))));
    var highY = Math.max(2, Math.ceil(Math.max(...observations.map(function (p) { return Math.abs(p.gear); }))));
    var lowX = -highX;
    var lowY = -highY;
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
      svg(plot, "text", {x: 43, y: 15}, "Écart d’équipement");
      svg(plot, "text", {x: width - 15, y: 263, "text-anchor": "end"}, "Écart au rythme médian (niv./7 j)");
      for (var tick = 0; tick <= 4; tick += 1) {
        var yValue = lowY + (highY - lowY) * tick / 4;
        var xValue = lowX + (highX - lowX) * tick / 4;
        svg(plot, "line", {x1: 43, x2: width - 13, y1: y(yValue), y2: y(yValue)});
        svg(plot, "text", {x: 35, y: y(yValue) + 4, "text-anchor": "end"}, axisFormat.format(yValue));
        svg(plot, "line", {x1: x(xValue), x2: x(xValue), y1: 26, y2: 233});
        svg(plot, "text", {x: x(xValue), y: 249, "text-anchor": "middle"}, format.format(xValue));
      }
      svg(plot, "line", {x1: x(lowX), x2: x(highX), y1: y(0), y2: y(0), style: "stroke:var(--gold);stroke-width:1.5"});
      svg(plot, "line", {x1: x(0), x2: x(0), y1: 26, y2: 233, style: "stroke:var(--gold);stroke-width:1.5"});
      var visible = frame.points.filter(function (point) { return picker.selected.has(String(point.id)); });
      empty.hidden = visible.length > 0;
      if (!empty.hidden) {
        empty.textContent = picker.selected.size > 0
          ? "Aucun relevé pour les personnages sélectionnés à cette date."
          : "Sélectionne au moins un personnage.";
      }
      visible.forEach(function (point) {
        var trail = [point];
        for (var i = index - 1; i >= Math.max(0, index - 3); i -= 1) {
          var previous = frames[i].points.find(function (item) { return item.id === point.id; });
          if (!previous) break;
          trail.unshift(previous);
        }
        if (trail.length > 1) svg(plot, "polyline", {
          points: trail.map(function (p) { return x(p.level) + "," + y(p.gear); }).join(" "),
          stroke: point.color, "stroke-width": 2, opacity: .42, fill: "none"
        });
        var dot = svg(plot, "circle", {cx: x(point.level), cy: y(point.gear), r: 7, fill: point.color});
        var label = point.name + " · " + format.format(point.level) + " niv./7 j vs médiane · "
          + format.format(point.gear) + " équipement vs tendance (" + axisFormat.format(point.equipment)
          + " relevé, " + axisFormat.format(point.expected) + " attendu) · " + axisFormat.format(point.days) + " j observés";
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
    picker.update();
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
  document.querySelectorAll("[data-tabs]").forEach(function (tabs) {
    var list = tabs.querySelector("[data-tablist]");
    var links = Array.prototype.slice.call(list.querySelectorAll("[data-tab]"));
    var panels = links.map(function (a) { return document.getElementById(a.hash.slice(1)); });

    list.setAttribute("role", "tablist");
    list.setAttribute("aria-label", tabs.querySelector(".tablist-wrap").getAttribute("aria-label"));
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
      if (push) {
        history.replaceState(null, "", links[index].hash);
        window.dispatchEvent(new Event("qg-tab-change"));
      }
    };
    var fromHash = function () {
      var target = document.getElementById(location.hash.slice(1));
      var i = target ? panels.findIndex(function (panel) { return panel.contains(target); }) : -1;
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
    window.addEventListener("qg-tab-change", function () { select(fromHash(), false, false); });
    window.addEventListener("hashchange", function () {
      select(fromHash(), false, false);
      var target = document.getElementById(location.hash.slice(1));
      if (target && panels[fromHash()].contains(target)) target.scrollIntoView({block: "start"});
    });
    select(fromHash(), false, false);
    var target = document.getElementById(location.hash.slice(1));
    if (target && panels[fromHash()].contains(target) && !target.hidden) target.scrollIntoView({block: "start"});
  });
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

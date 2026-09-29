// Section « Navettes » de l'écran Plan de Ramassage — indépendante de
// l'itinéraire du haut : ses propres adresses, sa propre destination.
//
// Le serveur (/tournees/navettes) géocode et répartit : le moins de navettes
// possible selon les places, chacune ordonnée jusqu'à la destination, à vol
// d'oiseau. Le navigateur refait ensuite chaque trajet par la route avec
// Google Maps — distances, durées, tracé sur la carte — et en déduit les
// heures de ramassage à rebours de l'heure d'arrivée.
//
// Les formats d'heure, de distance et de durée viennent de tours.js, chargé
// juste avant sur la même page (parseTime, formatTime, formatKm,
// formatDuration, googleAvailable).
(function () {
  const card = document.getElementById("shuttle-card");
  if (!card) return;

  const listEl = document.getElementById("shuttle-list");
  const template = document.getElementById("shuttle-stop-template");
  const resultsEl = document.getElementById("shuttle-results");
  const statusEl = document.getElementById("shuttle-status");
  const mapEl = document.getElementById("shuttle-map");
  const noteEl = document.getElementById("shuttle-map-note");
  const destination = document.getElementById("shuttle-destination");
  const arrival = document.getElementById("shuttle-arrival");
  const countField = document.getElementById("shuttle-count");
  const seatsField = document.getElementById("shuttle-seats");
  const runButton = document.getElementById("shuttle-run");

  const STORAGE_KEY = "kent.shuttles.v1";
  const MAX_STOPS = 40;
  // Une couleur par navette, reprises en boucle au-delà.
  const COLORS = ["#d6293a", "#1d63d8", "#1e8f4e", "#9a5b00", "#7b3fb8", "#0f8f9e", "#c2185b"];

  let map = null;
  let renderers = [];
  let markers = [];
  let saveTimer = null;

  // ------------------------------------------------------------ la liste
  function rows() {
    return Array.from(listEl.children);
  }

  function addressOf(row) {
    return row.querySelector(".shuttle-stop__address").value.trim();
  }

  function paxOf(row) {
    return Math.max(1, parseInt(row.querySelector(".shuttle-stop__pax").value, 10) || 1);
  }

  function filledRows() {
    return rows().filter(addressOf);
  }

  function renumber() {
    rows().forEach((row, index) => {
      row.querySelector(".shuttle-stop__rank").textContent = index + 1;
    });
  }

  function addRow(address, pax) {
    if (rows().length >= MAX_STOPS) {
      setStatus(`${MAX_STOPS} adresses au maximum.`, true);
      return null;
    }
    listEl.appendChild(template.content.cloneNode(true));
    const row = listEl.lastElementChild;
    if (address) row.querySelector(".shuttle-stop__address").value = address;
    if (pax) row.querySelector(".shuttle-stop__pax").value = pax;
    KentAddress.attach(row.querySelector(".shuttle-stop__address"), {
      provider: () => document.getElementById("tour-page").dataset.addressProvider,
      onPick: (item) => {
        row.querySelector(".shuttle-stop__address").value = item.label;
        save();
      },
    });
    renumber();
    return row;
  }

  function setStatus(text, isError) {
    statusEl.textContent = text || "";
    statusEl.hidden = !text;
    statusEl.classList.toggle("tour-status--error", !!isError);
  }

  // ------------------------------------------------------------ le calcul
  async function requestPlan(stops) {
    const body = new FormData();
    stops.forEach((stop) => {
      body.append("address", stop.address);
      body.append("pax", stop.pax);
    });
    body.append("destination", destination.value.trim());
    body.append("seats", seatsField.value || "8");
    if (countField.value) body.append("shuttles", countField.value);
    const resp = await fetch(card.dataset.url, { method: "POST", body });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) {
      markInvalid(data.failed_index);
      throw new Error(data.error || "Calcul impossible pour le moment.");
    }
    markInvalid(-1);
    return data;
  }

  function markInvalid(index) {
    filledRows().forEach((row, i) => row.classList.toggle("is-invalid", i === index));
  }

  // Itinéraire routier d'une navette : ses arrêts dans l'ordre, puis la
  // destination commune. L'ordre vient du serveur, le fournisseur ne le
  // rejuge pas. Les deux rendent le même objet : des étapes {km, seconds}
  // et de quoi dessiner (la réponse Google, ou un tracé de points TomTom).
  function googleRoute(addresses) {
    return new Promise((resolve, reject) => {
      const service = new google.maps.DirectionsService();
      service.route({
        origin: addresses[0],
        destination: destination.value.trim(),
        waypoints: addresses.slice(1).map((address) => ({ location: address, stopover: true })),
        optimizeWaypoints: false,
        travelMode: google.maps.TravelMode.DRIVING,
        region: "fr",
      }, (result, status) => {
        if (status !== "OK" || !result) { reject(new Error(status || "ZERO_RESULTS")); return; }
        const legs = (result.routes[0].legs || []).map((leg) => ({
          km: leg.distance.value / 1000, seconds: leg.duration.value, start: leg.start_location,
        }));
        resolve({ legs, result });
      });
    });
  }

  async function tomtomRoute(addresses) {
    const body = new FormData();
    addresses.concat([destination.value.trim()]).forEach((a) => body.append("address", a));
    const resp = await fetch(document.getElementById("tour-page").dataset.routeUrl,
                             { method: "POST", body });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error(data.error || "Itinéraire TomTom indisponible.");
    return { legs: data.legs, path: data.path, points: data.points };
  }

  function roadRoute(addresses) {
    if (routeProvider() === "tomtom") return tomtomRoute(addresses);
    if (googleAvailable()) return googleRoute(addresses);
    return Promise.reject(new Error("Aucun fournisseur d'itinéraire."));
  }

  // Heures de passage, à rebours de l'heure d'arrivée : la dernière étape
  // mène à la destination, on remonte étape par étape.
  function pickupTimes(durations) {
    const end = parseTime(arrival.value);
    if (end == null) return null;
    const times = [];
    let minutes = end;
    for (let i = durations.length - 1; i >= 0; i--) {
      minutes -= durations[i] / 60;
      times.unshift(formatTime(minutes));
    }
    return times;
  }

  // ---------------------------------------------------------- l'affichage
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function mapsUrl(addresses) {
    const params = new URLSearchParams({
      api: "1", travelmode: "driving",
      origin: addresses[0], destination: destination.value.trim(),
    });
    const waypoints = addresses.slice(1, 10);
    if (waypoints.length) params.set("waypoints", waypoints.join("|"));
    return "https://www.google.com/maps/dir/?" + params.toString();
  }

  // Une carte par navette : en-tête (numéro, voyageurs, distance, durée),
  // arrêts dans l'ordre avec leur heure, puis la destination.
  function renderShuttle(index, shuttle, stops, road) {
    const addresses = shuttle.stops.map((i) => stops[i].address);
    const color = COLORS[index % COLORS.length];
    const box = el("div", "shuttle-result");
    box.style.setProperty("--shuttle-color", color);

    const head = el("div", "shuttle-result__head");
    head.append(el("span", "shuttle-result__name", `Navette ${index + 1}`),
                el("span", "shuttle-result__pax", `${shuttle.passengers} voyageur${shuttle.passengers > 1 ? "s" : ""}`));
    if (road) {
      const km = road.legs.reduce((sum, leg) => sum + leg.km, 0);
      const seconds = road.legs.reduce((sum, leg) => sum + leg.seconds, 0);
      head.append(el("span", "shuttle-result__meta",
                     `${formatKm(km)} · ${formatDuration(seconds)} de route`));
    } else {
      head.append(el("span", "shuttle-result__meta", `≈ ${formatKm(shuttle.total_km)} à vol d'oiseau`));
    }
    const link = el("a", "btn btn--blue btn--sm", "Ouvrir dans Google Maps");
    link.href = mapsUrl(addresses);
    link.target = "_blank";
    link.rel = "noopener";
    head.appendChild(link);
    box.appendChild(head);

    const times = road ? pickupTimes(road.legs.map((leg) => leg.seconds)) : null;
    const list = el("ol", "shuttle-result__stops");
    shuttle.stops.forEach((stopIndex, position) => {
      const line = el("li");
      if (times) line.appendChild(el("span", "shuttle-result__time", times[position]));
      line.appendChild(el("span", "shuttle-result__address", stops[stopIndex].address));
      line.appendChild(el("span", "shuttle-result__count", `${stops[stopIndex].pax} pax`));
      list.appendChild(line);
    });
    const last = el("li", "shuttle-result__end");
    if (times) last.appendChild(el("span", "shuttle-result__time", formatTime(parseTime(arrival.value))));
    last.appendChild(el("span", "shuttle-result__address", destination.value.trim()));
    list.appendChild(last);
    box.appendChild(list);
    return box;
  }

  // Repère numéroté, à la couleur de sa navette : deux navettes se croisent
  // souvent sur la même route, des pastilles identiques ne se distinguent
  // plus (et leur numérotation repart à 1 pour chacune).
  function marker(position, label, color, title) {
    return new google.maps.Marker({
      position, map, title,
      label: { text: String(label), color: "#fff", fontSize: "11px", fontWeight: "700" },
      icon: {
        path: google.maps.SymbolPath.CIRCLE, scale: 10,
        fillColor: color, fillOpacity: 1, strokeColor: "#fff", strokeWeight: 2,
      },
    });
  }

  function clearMap() {
    renderers.forEach((renderer) => renderer.setMap(null));
    markers.forEach((marker) => marker.setMap(null));
    renderers = [];
    markers = [];
  }

  function ensureMap() {
    if (map || !googleAvailable()) return map;
    mapEl.hidden = false;
    map = new google.maps.Map(mapEl, {
      center: { lat: 46.9, lng: 2.4 }, zoom: 5,
      mapTypeControl: false, streetViewControl: false,
    });
    return map;
  }

  // Tracés routiers, une couleur par navette. `preserveViewport` (côté
  // Google) : sans lui, chaque tracé recadre la carte sur lui-même et seule
  // la dernière navette reste visible — le cadrage est fait une fois, sur
  // l'ensemble.
  function drawRoutes(roads) {
    if (!ensureMap()) return;
    clearMap();
    const bounds = new google.maps.LatLngBounds();
    roads.forEach((road, index) => {
      if (!road) return;
      const color = COLORS[index % COLORS.length];
      if (road.result) {
        renderers.push(new google.maps.DirectionsRenderer({
          map, directions: road.result, suppressMarkers: true, preserveViewport: true,
          polylineOptions: { strokeColor: color, strokeWeight: 5, strokeOpacity: .85 },
        }));
        if (road.result.routes[0].bounds) bounds.union(road.result.routes[0].bounds);
        road.legs.forEach((leg, position) => {
          markers.push(marker(leg.start, position + 1, color,
                              `Navette ${index + 1} — arrêt ${position + 1}`));
        });
      } else if (road.path) {
        renderers.push(new google.maps.Polyline({
          map, path: road.path, strokeColor: color, strokeWeight: 5, strokeOpacity: .85,
        }));
        road.path.forEach((point) => bounds.extend(point));
        // Le dernier point rendu est la destination, commune à toutes.
        road.points.slice(0, -1).forEach((point, position) => {
          markers.push(marker(point, position + 1, color,
                              `Navette ${index + 1} — arrêt ${position + 1}`));
        });
      }
    });
    if (!bounds.isEmpty()) map.fitBounds(bounds, 48);
  }

  // Repli sans itinéraire routier : un repère par arrêt, plus la destination.
  function drawPoints(data, shuttles) {
    if (!ensureMap()) return;
    clearMap();
    const bounds = new google.maps.LatLngBounds();
    shuttles.forEach((shuttle, index) => {
      const path = shuttle.stops.map((i) => data.points[i]).concat([data.destination]);
      renderers.push(new google.maps.Polyline({
        map, path, strokeColor: COLORS[index % COLORS.length], strokeOpacity: .8, strokeWeight: 3,
      }));
      shuttle.stops.forEach((i, position) => {
        markers.push(marker(data.points[i], position + 1, COLORS[index % COLORS.length],
                            `Navette ${index + 1} — arrêt ${position + 1}`));
        bounds.extend(data.points[i]);
      });
    });
    markers.push(new google.maps.Marker({ position: data.destination, map, title: "Destination" }));
    bounds.extend(data.destination);
    map.fitBounds(bounds, 48);
  }

  // ------------------------------------------------------------ la marche
  async function run() {
    rows().forEach((row) => { if (!addressOf(row)) row.remove(); });
    if (!rows().length) addRow("", 1);
    renumber();
    const stops = filledRows().map((row) => ({ address: addressOf(row), pax: paxOf(row) }));
    if (!stops.length) { setStatus("Ajoutez au moins une adresse de ramassage.", true); return; }
    if (!destination.value.trim()) { setStatus("Renseignez la destination finale.", true); return; }

    runButton.disabled = true;
    setStatus("Répartition des voyageurs…");
    resultsEl.textContent = "";
    try {
      const data = await requestPlan(stops);
      let roads = data.shuttles.map(() => null);
      if (routeProvider() === "tomtom" || googleAvailable()) {
        setStatus(`Itinéraires par la route (${routeProvider() === "tomtom" ? "TomTom" : "Google Maps"})…`);
        roads = await Promise.all(data.shuttles.map((shuttle) =>
          roadRoute(shuttle.stops.map((i) => stops[i].address)).catch(() => null)));
      }
      const road = roads.some(Boolean) ? roads : null;
      data.shuttles.forEach((shuttle, index) => {
        resultsEl.appendChild(renderShuttle(index, shuttle, stops, road ? roads[index] : null));
      });
      if (road) drawRoutes(roads); else drawPoints(data, data.shuttles);

      const passengers = stops.reduce((sum, stop) => sum + stop.pax, 0);
      const known = roads.filter(Boolean);
      const km = known.length === roads.length
        ? known.reduce((sum, r) => sum + r.legs.reduce((s, l) => s + l.km, 0), 0)
        : data.shuttles.reduce((sum, s) => sum + s.total_km, 0);
      setStatus(`${data.shuttles.length} navette${data.shuttles.length > 1 ? "s" : ""} `
        + `pour ${passengers} voyageur${passengers > 1 ? "s" : ""} `
        + `(${data.seats} places) — ${known.length === roads.length ? "" : "≈ "}${formatKm(km)} en tout.`);
      // Rien à dire quand tout s'est bien passé : le résultat se lit seul.
      noteEl.textContent = known.length === roads.length
        ? ""
        : "Itinéraire routier indisponible : distances à vol d'oiseau, sans heures de ramassage.";
      save();
    } catch (e) {
      setStatus(e.message, true);
      resultsEl.textContent = "";
      clearMap();
    } finally {
      runButton.disabled = false;
    }
  }

  // --------------------------------------------------------- mémorisation
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          stops: rows().map((row) => ({ address: addressOf(row), pax: paxOf(row) })),
          destination: destination.value,
          arrival: arrival.value,
          count: countField.value,
          seats: seatsField.value,
        }));
      } catch (e) { /* navigation privée : tant pis */ }
    }, 400);
  }

  function restore() {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    } catch (e) { saved = null; }
    const stops = saved && Array.isArray(saved.stops) ? saved.stops.slice(0, MAX_STOPS) : [];
    (stops.length ? stops : [{ address: "", pax: 1 }, { address: "", pax: 1 }])
      .forEach((stop) => addRow(stop.address, stop.pax));
    if (saved) {
      destination.value = saved.destination || "";
      arrival.value = saved.arrival || "";
      countField.value = saved.count || "";
      seatsField.value = saved.seats || "8";
    }
  }

  function clearAll() {
    if (!confirm("Vider les adresses de ramassage et les réglages des navettes ?")) return;
    rows().forEach((row) => row.remove());
    addRow("", 1);
    addRow("", 1);
    destination.value = "";
    arrival.value = "";
    countField.value = "";
    seatsField.value = "8";
    resultsEl.textContent = "";
    clearMap();
    mapEl.hidden = true;
    map = null;
    setStatus("");
    noteEl.textContent = "";
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) { /* tant pis */ }
  }

  // ------------------------------------------------------------------ init
  restore();
  KentAddress.attach(destination, {
    provider: () => document.getElementById("tour-page").dataset.addressProvider,
    onPick: (item) => { destination.value = item.label; save(); },
  });

  document.getElementById("shuttle-add").addEventListener("click", () => {
    const row = addRow("", 1);
    if (row) row.querySelector(".shuttle-stop__address").focus();
  });
  document.getElementById("shuttle-clear").addEventListener("click", clearAll);
  runButton.addEventListener("click", run);

  listEl.addEventListener("click", (e) => {
    if (!e.target.matches(".row-remove")) return;
    e.target.closest("li").remove();
    if (!rows().length) addRow("", 1);
    renumber();
    save();
  });
  listEl.addEventListener("input", save);
  [destination, countField, seatsField].forEach((field) => field.addEventListener("input", save));

  // Heure d'arrivée : même écriture que les heures de l'itinéraire du haut
  // (« 630 » ou « 6h30 » donnent « 06h30 »).
  arrival.addEventListener("input", save);
  arrival.addEventListener("blur", () => {
    const minutes = parseTime(arrival.value);
    if (minutes != null) arrival.value = formatTime(minutes);
    save();
  });

  // Entrée dans une adresse : passer à la ligne suivante, ou en créer une.
  listEl.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target.matches(".shuttle-stop__address")) return;
    e.preventDefault();
    KentAddress.close();
    const next = e.target.closest("li").nextElementSibling || addRow("", 1);
    if (next) next.querySelector(".shuttle-stop__address").focus();
  });
})();

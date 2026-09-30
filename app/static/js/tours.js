// Écran Plan de Ramassage : une liste d'adresses, l'ordre de passage le plus
// court, et la carte de l'itinéraire.
//
// Rien n'est imposé : le calcul choisit aussi par où commencer et par où
// finir. Un itinéraire Google Maps, lui, exige un départ et une arrivée
// connus — d'où le déroulé en deux temps :
// 1. /tournees/optimiser (côté serveur) géocode les adresses et les ordonne à
//    vol d'oiseau, les deux bouts compris ;
// 2. Google Maps réoptimise le milieu du parcours sur les distances routières
//    réelles (`optimizeWaypoints`, les deux bouts restant ceux du serveur) et
//    en trace la carte. Sans clé, ou si l'API Directions est refusée, l'ordre
//    du serveur reste tel quel et la carte se limite à des repères numérotés
//    reliés en pointillés.
//
// Le second bouton, « Calculer l'itinéraire », s'arrête au point 2 sans
// réoptimiser : l'ordre affiché est celui qu'on veut suivre.
//
// Le glisser-déposer ne relance jamais l'optimisation : il redessine, lui
// aussi, l'itinéraire de l'ordre affiché, pour que la carte et les distances
// correspondent toujours à la liste qu'on a sous les yeux.

const page = document.getElementById("tour-page");
const listEl = document.getElementById("tour-list");
const stopTemplate = document.getElementById("tour-stop-template");
const summaryEl = document.getElementById("tour-summary");
const statusEl = document.getElementById("tour-status");
const mapsLink = document.getElementById("tour-maps-link");
const copyButton = document.getElementById("tour-copy");
const omButton = document.getElementById("tour-om");
const mapNote = document.getElementById("tour-map-note");

const MAX_STOPS = parseInt(page.dataset.maxStops, 10) || 25;
// Google Maps n'accepte que 9 étapes intermédiaires dans une URL
// d'itinéraire (api=1), là où la carte de la page en affiche MAX_STOPS.
const MAPS_URL_WAYPOINTS = 9;
const STORAGE_KEY = "kent.tour.v1";

let map = null;
let directionsService = null;
let directionsRenderer = null;
let markers = [];
let straightLine = null;
// Coordonnées déjà connues, par adresse : évite de redemander un géocodage
// au serveur à chaque glisser-déposer.
const knownCoords = new Map();
// Un itinéraire est affiché : tout changement d'ordre le rafraîchit.
let computed = false;
// Directions a refusé de répondre : inutile de le rappeler à chaque
// rafraîchissement, la carte se contentera des pointillés.
let directionsRefused = false;
let saveTimer = null;
// Ligne dont l'heure a été saisie à la main en dernier : c'est elle qui cale
// toutes les autres.
let anchorRow = null;

function googleAvailable() {
  return !!(window.google && google.maps);
}

// Qui trace les itinéraires : Google Maps dans le navigateur, ou TomTom
// côté serveur (/tournees/itineraire). Le choix vaut pour toute la page,
// section Navettes comprise, et reste sur l'appareil.
const PROVIDER_KEY = "kent.routeProvider";

function providerSelects() {
  return Array.from(document.querySelectorAll("[data-route-provider]"));
}

function routeProvider() {
  const select = providerSelects()[0];
  return select ? select.value : "google";
}

// Un réglage, un sélecteur par section : ils affichent toujours la même
// chose, et le choix est gardé par l'appareil.
function initRouteProvider() {
  const selects = providerSelects();
  if (!selects.length) return;
  let saved = null;
  try {
    saved = localStorage.getItem(PROVIDER_KEY);
  } catch (e) { /* rien de mémorisé */ }
  if (saved && selects[0].querySelector(`option[value="${saved}"]:not([disabled])`)) {
    selects.forEach((select) => { select.value = saved; });
  }
  selects.forEach((select) => {
    select.addEventListener("change", () => {
      selects.forEach((other) => { other.value = select.value; });
      try {
        localStorage.setItem(PROVIDER_KEY, select.value);
      } catch (e) { /* tant pis */ }
      // L'itinéraire affiché vient de l'autre fournisseur : il est à refaire.
      if (computed) {
        setStatus("Fournisseur changé : relancez le calcul de l'itinéraire.");
        clearResult();
      }
    });
  });
}

// Itinéraire TomTom d'une liste d'adresses, dans l'ordre : étapes (km,
// durée) et tracé, calculés par le serveur.
async function requestTomTom(stops) {
  const body = new FormData();
  stops.map(forGoogle).forEach((address) => body.append("address", address));
  const resp = await fetch(page.dataset.routeUrl, { method: "POST", body });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data.ok) {
    if (data.failed_index != null) markInvalid(data.failed_index);
    throw new Error(data.error || "Itinéraire TomTom indisponible.");
  }
  markInvalid(-1);
  return data;
}

// --------------------------------------------------------------- la liste
function rows() {
  return Array.from(listEl.children);
}

function addressOf(row) {
  return row.querySelector(".tour-stop__address").value.trim();
}

// ------------------------------------------------------------- les heures
// Une seule heure est saisie ; les autres se déduisent des durées de trajet
// (celles de Google), en avant comme en arrière. `data-auto` distingue les
// heures posées par le calcul de celle qu'on a tapée, qui ne bouge pas.
function timeOf(row) {
  return row.querySelector(".tour-stop__time").value;
}

// Heure saisie librement : « 6 », « 630 », « 0630 », « 6:30 » ou « 6h30 »
// valent tous 06:30. Minutes depuis minuit, ou null si ce n'est pas une heure.
function parseTime(text) {
  const parts = (text || "").trim().split(/[\s:hH.,]+/).filter(Boolean);
  let hours, minutes;
  if (parts.length >= 2) {
    hours = Number(parts[0]);
    minutes = Number(parts[1]);
  } else {
    const digits = parts[0] || "";
    if (!/^\d{1,4}$/.test(digits)) return null;
    hours = Number(digits.length > 2 ? digits.slice(0, -2) : digits);
    minutes = digits.length > 2 ? Number(digits.slice(-2)) : 0;
  }
  if (!Number.isInteger(hours) || !Number.isInteger(minutes) || hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

// Minutes depuis minuit -> « 18h30 », toujours sur 24 heures : la notation
// du reste de l'application (app/utils.py:fmt_time).
function formatTime(minutes) {
  const day = ((Math.round(minutes) % 1440) + 1440) % 1440;  // une tournée peut passer minuit
  return `${String(Math.floor(day / 60)).padStart(2, "0")}h${String(day % 60).padStart(2, "0")}`;
}

function minutesOf(row) {
  return parseTime(timeOf(row));
}

function setComputedTime(row, minutes) {
  const field = row.querySelector(".tour-stop__time");
  field.value = formatTime(minutes);
  field.dataset.auto = "1";
}

// Heure de référence : la dernière saisie à la main si elle est toujours là,
// sinon la première heure que le calcul n'a pas posée (liste rechargée).
function anchorIn(all) {
  if (anchorRow && all.includes(anchorRow) && timeOf(anchorRow)) return anchorRow;
  return all.find((row) => timeOf(row) && !row.querySelector(".tour-stop__time").dataset.auto) || null;
}

// `seconds[i]` : durée de la ligne i à la ligne i+1.
function applyTimes(seconds) {
  const all = filledRows();
  const anchor = anchorIn(all);
  const at = all.indexOf(anchor);
  if (at < 0 || seconds.length < all.length - 1) return;
  const base = minutesOf(anchor);
  if (base == null) return;
  anchor.querySelector(".tour-stop__time").value = formatTime(base);
  let minutes = base;
  for (let i = at + 1; i < all.length; i++) {
    minutes += seconds[i - 1] / 60;
    setComputedTime(all[i], minutes);
  }
  minutes = base;
  for (let i = at - 1; i >= 0; i--) {
    minutes -= seconds[i] / 60;
    setComputedTime(all[i], minutes);
  }
  save();  // les heures calculées sont retrouvées au prochain passage
}

function clearComputedTimes() {
  rows().forEach((row) => {
    const field = row.querySelector(".tour-stop__time");
    if (field.dataset.auto) { field.value = ""; delete field.dataset.auto; }
  });
}

// Les lignes qui portent une adresse : les seules géocodées, tracées et
// mesurées. Une ligne encore vide garde sa place dans la liste sans
// décaler les étapes affichées.
function filledRows() {
  return rows().filter(addressOf);
}

function addresses() {
  return filledRows().map(addressOf);
}

function addRow(value) {
  if (rows().length >= MAX_STOPS) {
    setStatus(`${MAX_STOPS} arrêts au maximum : Google Maps n'en calcule pas plus.`, true);
    return null;
  }
  listEl.appendChild(stopTemplate.content.cloneNode(true));
  const row = listEl.lastElementChild;
  if (value) row.querySelector(".tour-stop__address").value = value;
  attachSuggestions(row);
  renumber();
  return row;
}

// Numéro, rôle (départ / arrivée) et état des flèches : tout ce qui dépend
// de la position d'une ligne, rejoué après chaque changement d'ordre.
function renumber() {
  const all = rows();
  const filled = filledRows();
  all.forEach((row, index) => {
    row.querySelector(".tour-stop__rank").textContent = index + 1;
    row.querySelector(".row-up").disabled = index === 0;
    row.querySelector(".row-down").disabled = index === all.length - 1;
    // Les deux bouts ne sont pas imposés : ces étiquettes décrivent
    // l'itinéraire affiché, elles ne le contraignent pas.
    let role = "";
    if (filled.length > 1 && row === filled[0]) role = "Départ";
    else if (filled.length > 1 && row === filled[filled.length - 1]) role = "Arrivée";
    row.querySelector(".tour-stop__role").textContent = role;
  });
}

function removeRow(row) {
  row.remove();
  if (!rows().length) addRow("");
  onOrderChanged();
}

function moveRow(row, direction) {
  if (direction < 0 && row.previousElementSibling) {
    listEl.insertBefore(row, row.previousElementSibling);
  } else if (direction > 0 && row.nextElementSibling) {
    listEl.insertBefore(row.nextElementSibling, row);
  } else {
    return;
  }
  onOrderChanged();
}

// Remet les lignes dans l'ordre donné (indices de la liste affichée, telle
// qu'elle a été envoyée au calcul — donc sans ligne vide). Les <li> sont
// déplacés et non recréés : la saisie en cours et le focus restent.
function applyOrder(order) {
  const current = rows();
  const moved = document.createDocumentFragment();
  order.forEach((index) => moved.appendChild(current[index]));
  listEl.appendChild(moved);
  renumber();
}

// Avant un calcul : les lignes vides n'ont rien à y faire, et les retirer
// fait correspondre les indices de l'ordre renvoyé aux lignes de l'écran.
function pruneEmptyRows() {
  rows().forEach((row) => { if (!addressOf(row)) row.remove(); });
  if (!rows().length) addRow("");
  renumber();
}

// Adresse que le géocodage n'a pas reconnue : on la montre plutôt que de
// parler d'« une des adresses ». L'index est celui de la liste envoyée au
// serveur, donc des lignes renseignées.
function markInvalid(index) {
  rows().forEach((row) => row.classList.remove("is-invalid"));
  const row = filledRows()[index];
  if (row) row.classList.add("is-invalid");
}

// ------------------------------------------------------- état et messages
function setStatus(text, isError) {
  statusEl.textContent = text || "";
  statusEl.hidden = !text;
  statusEl.classList.toggle("tour-status--error", !!isError);
}

function setSummary(parts) {
  summaryEl.textContent = "";
  parts.forEach((part, index) => {
    if (index) {
      const sep = document.createElement("span");
      sep.className = "legs-summary__sep";
      sep.textContent = "·";
      summaryEl.appendChild(sep);
    }
    summaryEl.appendChild(part);
  });
  summaryEl.hidden = !parts.length;
  // Les actions du résultat n'ont de sens qu'une fois l'itinéraire calculé.
  mapsLink.hidden = !parts.length;
  copyButton.hidden = !parts.length;
  omButton.hidden = !parts.length;
}

// « 87,4 km » en gras suivi de son libellé : de quoi lire le récap d'un
// coup d'œil, sans injecter de HTML construit à la main.
function summaryItem(value, label) {
  const span = document.createElement("span");
  const strong = document.createElement("strong");
  strong.textContent = value;
  span.appendChild(strong);
  if (label) span.appendChild(document.createTextNode(" " + label));
  return span;
}

function summaryNote(text) {
  const span = document.createElement("span");
  span.className = "muted";
  span.textContent = text;
  return span;
}

function formatKm(km) {
  return km.toLocaleString("fr-FR", { maximumFractionDigits: 1 }) + " km";
}

function formatDuration(seconds) {
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
}

function stopCount(count) {
  return `${count} arrêt${count > 1 ? "s" : ""}`;
}

// Ce qu'il reste à faire sous chaque adresse, jusqu'à la suivante :
// `legs[i]` va de la ligne i à la ligne i+1 — le dernier arrêt n'en a donc pas.
function setLegs(legs) {
  filledRows().forEach((row, index) => {
    const el = row.querySelector(".tour-stop__leg");
    el.textContent = legs[index] ? "↓ " + legs[index] : "";
  });
}

function clearLegs() {
  rows().forEach((row) => { row.querySelector(".tour-stop__leg").textContent = ""; });
}

function clearResult() {
  clearComputedTimes();  // elles ne valaient que pour l'itinéraire effacé
  clearLegs();
  setSummary([]);
  clearMap();
  computed = false;
}

// ---------------------------------------------------------------- la carte
function initMap() {
  const container = document.getElementById("tour-map");
  if (!googleAvailable()) {
    container.classList.add("tour-map--off");
    container.textContent = "Carte indisponible : aucune clé Google Maps configurée (GOOGLE_MAPS_API_KEY).";
    mapNote.textContent = "L'ordre de passage, lui, est calculé côté serveur : l'écran reste utilisable.";
    return;
  }
  map = new google.maps.Map(container, {
    // La France entière tant qu'aucun itinéraire n'est calculé ; la vue se
    // recadre sur la tournée dès le premier calcul.
    center: { lat: 46.9, lng: 2.4 },
    zoom: 5,
    mapTypeControl: false,
    streetViewControl: false,
  });
}

function clearMap() {
  markers.forEach((marker) => marker.setMap(null));
  markers = [];
  if (straightLine) { straightLine.setMap(null); straightLine = null; }
  if (directionsRenderer) { directionsRenderer.setMap(null); directionsRenderer = null; }
}

// Repères numérotés comme la liste — Google, laissé à lui-même, les marque
// A, B, C… ce qui ne se recoupe plus avec les numéros de l'écran.
function placeMarkers(positions) {
  const labels = addresses();
  positions.forEach((position, index) => {
    markers.push(new google.maps.Marker({
      position, map, label: String(index + 1), title: `${index + 1}. ${labels[index] || ""}`,
    }));
  });
}

// Tracé routier, dessiné par Google à partir de sa propre réponse.
function drawGoogleRoute(result) {
  clearMap();
  directionsRenderer = new google.maps.DirectionsRenderer({
    map, directions: result, suppressMarkers: true,
  });
  // Une étape part de chaque arrêt sauf le dernier, qui est l'arrivée de la
  // dernière étape.
  const legs = result.routes[0].legs || [];
  const positions = legs.map((leg) => leg.start_location);
  if (legs.length) positions.push(legs[legs.length - 1].end_location);
  placeMarkers(positions);
}

// Tracé TomTom : la route telle qu'il la rend, plus les repères numérotés.
function drawTomTomRoute(path, points) {
  clearMap();
  if (!map) return;
  const bounds = new google.maps.LatLngBounds();
  placeMarkers(points);
  points.forEach((point) => bounds.extend(point));
  straightLine = new google.maps.Polyline({
    path, map, strokeColor: "#1d63d8", strokeOpacity: .85, strokeWeight: 5,
  });
  path.forEach((point) => bounds.extend(point));
  if (points.length > 1) map.fitBounds(bounds, 48);
}

// Résultat TomTom : mêmes informations que celui de Google, à la source près.
function showTomTomResult(data, stops) {
  const km = data.legs.reduce((total, leg) => total + leg.km, 0);
  const seconds = data.legs.reduce((total, leg) => total + leg.seconds, 0);
  drawTomTomRoute(data.path, data.points);
  setLegs(data.legs.map((leg) => `${formatKm(leg.km)} · ${formatDuration(leg.seconds)}`));
  applyTimes(data.legs.map((leg) => leg.seconds));
  setSummary([
    summaryItem(formatKm(km), "par la route"),
    summaryItem(formatDuration(seconds), "de conduite"),
    summaryItem(stopCount(stops.length), ""),
  ]);
  setStatus("");
  mapNote.textContent = "";  // le résultat parle de lui-même ; la note ne sert qu'en cas d'ennui
  computed = true;
  updateMapsLink();
}

// Repli : un repère numéroté par arrêt, reliés en pointillés — le trait ne
// prétend pas suivre la route.
function drawStraightRoute(points) {
  clearMap();
  if (!map) return;
  const bounds = new google.maps.LatLngBounds();
  placeMarkers(points);
  points.forEach((point) => bounds.extend(point));
  straightLine = new google.maps.Polyline({
    path: points,
    map,
    strokeOpacity: 0,
    icons: [{
      icon: { path: "M 0,-1 0,1", strokeColor: "#d6293a", strokeOpacity: 0.9, scale: 3 },
      offset: "0", repeat: "14px",
    }],
  });
  if (points.length > 1) map.fitBounds(bounds, 48);
}

// -------------------------------------------------- appels aux deux moteurs
// « Dépôt KENT » est un libellé interne, hérité des trajets de l'ordre de
// mission : Google ne le connaît pas et refuserait tout l'itinéraire
// (NOT_FOUND). On lui substitue l'adresse de l'entreprise, comme le fait le
// serveur (app/routing.py:normalize_place).
const DEPOT_FOLD = "depot kent";

function forGoogle(address) {
  const folded = address.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  return folded === DEPOT_FOLD ? (page.dataset.depotAddress || address) : address;
}

// Itinéraire Google pour les adresses données, dans l'ordre reçu. Avec
// `optimize`, Google réordonne les arrêts intermédiaires — les deux bouts
// restent ceux que le serveur a choisis, Directions ne sachant pas faire
// autrement.
function requestDirections(stops, optimize) {
  return new Promise((resolve, reject) => {
    const places = stops.map(forGoogle);
    if (!directionsService) directionsService = new google.maps.DirectionsService();
    directionsService.route({
      origin: places[0],
      destination: places[places.length - 1],
      waypoints: places.slice(1, -1).map((address) => ({ location: address, stopover: true })),
      optimizeWaypoints: !!optimize,
      travelMode: google.maps.TravelMode.DRIVING,
      region: "fr",
    }, (result, status) => {
      if (status === "OK" && result) resolve(result);
      else reject(new Error(status || "ZERO_RESULTS"));
    });
  });
}

// `optimize` faux : le serveur garde l'ordre envoyé et ne renvoie que les
// coordonnées et les distances (rafraîchissement de la carte).
async function requestServer(stops, optimize) {
  const body = new FormData();
  body.append("optimize", optimize ? "1" : "0");
  stops.forEach((address) => body.append("address", address));
  const resp = await fetch(page.dataset.optimizeUrl, { method: "POST", body });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data.ok) {
    if (data.failed_index != null) markInvalid(data.failed_index);
    throw new Error(data.error || "Calcul impossible pour le moment.");
  }
  markInvalid(-1);
  (data.points || []).forEach((point, index) => knownCoords.set(stops[index], point));
  return data;
}

// Google n'a pas répondu. Un refus (clé sans l'API Directions, quota) vaut
// pour toute la session : on le dit une fois, puis on s'en passe.
function noteDirectionsFailure(error) {
  if (["REQUEST_DENIED", "OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"].includes(error.message)) {
    directionsRefused = true;
    mapNote.textContent = "Itinéraire routier indisponible : l'API « Directions » n'est pas activée sur "
      + "la clé Google Maps, ou son quota est atteint. La carte relie les arrêts en pointillés et les "
      + "distances sont à vol d'oiseau.";
    return;
  }
  mapNote.textContent = error.message === "ZERO_RESULTS"
    ? "Google Maps ne trouve pas de route entre ces arrêts : la carte les relie en pointillés."
    : `Itinéraire routier indisponible pour cette liste (${error.message}) : la carte relie les arrêts en pointillés.`;
}

// ------------------------------------------------------------- le calcul
// Mécanique commune aux deux boutons : on nettoie la liste, on vérifie qu'il
// y a de quoi tracer un trajet, puis `run` fait le travail.
async function calculate(run, message) {
  pruneEmptyRows();
  const stops = addresses();
  if (stops.length < 2) {
    setStatus("Saisissez au moins deux adresses.", true);
    return;
  }
  if (stops.length > MAX_STOPS) {
    setStatus(`${MAX_STOPS} arrêts au maximum.`, true);
    return;
  }

  const buttons = [document.getElementById("tour-optimize"), document.getElementById("tour-trace")];
  buttons.forEach((button) => { button.disabled = true; });
  setStatus(message);
  try {
    await run(stops);
  } catch (e) {
    setStatus(e.message, true);
    clearResult();
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

// « Calculer l'itinéraire le plus court » : l'ordre de la liste est remis en
// cause. Le serveur le choisit en entier, premier et dernier arrêt compris ;
// Google, si elle répond, réoptimise ensuite le milieu sur les vraies
// distances routières et fournit le tracé et les kilomètres.
function optimize() {
  return calculate(async (stops) => {
    const data = await requestServer(stops, true);
    applyOrder(data.order);
    save();
    await refreshRoute(data, true);
  }, "Calcul de l'itinéraire le plus court…");
}

// « Calculer l'itinéraire » : l'ordre affiché est celui qu'on veut suivre —
// on ne calcule que le trajet, la carte et les distances.
function traceCurrentOrder() {
  return calculate(() => refreshRoute(), "Calcul de l'itinéraire…");
}

// Résultat Google. `reordered` : la réponse vient d'une demande optimisée,
// l'ordre des lignes est donc à reprendre de `waypoint_order` (qui ne
// concerne que les arrêts intermédiaires — les deux bouts restent ceux que le
// serveur a choisis).
function showGoogleResult(result, reordered) {
  if (reordered) {
    const stops = addresses();
    const waypointOrder = result.routes[0].waypoint_order || [];
    const order = [0].concat(waypointOrder.map((index) => index + 1), [stops.length - 1]);
    if (order.length === stops.length) applyOrder(order);
    save();
  }

  const legs = result.routes[0].legs || [];
  const meters = legs.reduce((total, leg) => total + leg.distance.value, 0);
  const seconds = legs.reduce((total, leg) => total + leg.duration.value, 0);
  drawGoogleRoute(result);
  setLegs(legs.map((leg) => `${formatKm(leg.distance.value / 1000)} · ${formatDuration(leg.duration.value)}`));
  applyTimes(legs.map((leg) => leg.duration.value));
  setSummary([
    summaryItem(formatKm(meters / 1000), "par la route"),
    summaryItem(formatDuration(seconds), "de conduite"),
    summaryItem(stopCount(addresses().length), ""),
  ]);
  setStatus("");
  mapNote.textContent = "";
  computed = true;
  updateMapsLink();
}

// Repli : distances à vol d'oiseau, calculées par le serveur. `ordered` :
// c'est lui aussi qui a décidé de l'ordre, ce qui mérite d'être dit.
function showApproxResult(data, stops, ordered) {
  const points = stops
    .map((address) => (knownCoords.has(address) ? Object.assign({ address }, knownCoords.get(address)) : null))
    .filter(Boolean);
  if (points.length === stops.length) drawStraightRoute(points);
  setLegs((data.legs_km || []).map((km) => `≈ ${formatKm(km)} à vol d'oiseau`));
  const parts = [
    summaryItem("≈ " + formatKm(data.total_km || 0), "à vol d'oiseau"),
    summaryItem(stopCount(stops.length), ""),
  ];
  if (ordered) parts.push(summaryNote("ordre calculé sans le réseau routier"));
  setSummary(parts);
  setStatus(filledRows().some((row) => timeOf(row))
    ? "Heures non calculées : elles demandent les durées de l'itinéraire routier."
    : "");
  computed = true;
  updateMapsLink();
}

// Trace l'itinéraire de l'ordre affiché. Avec `refine`, Google a le droit de
// réordonner le milieu du parcours — c'est le cas juste après un calcul ;
// sinon l'ordre est intouché, comme après un glisser-déposer, une flèche ▲▼
// ou une suppression.
async function refreshRoute(approxData, refine) {
  const stops = addresses();
  if (stops.length < 2) {
    clearResult();
    setStatus("");
    return;
  }
  if (routeProvider() === "tomtom") {
    try {
      showTomTomResult(await requestTomTom(stops), stops);
      return;
    } catch (e) {
      mapNote.textContent = e.message + " La carte relie les arrêts en pointillés.";
    }
  } else if (googleAvailable() && !directionsRefused) {
    try {
      showGoogleResult(await requestDirections(stops, !!refine), !!refine);
      return;
    } catch (e) {
      noteDirectionsFailure(e);
    }
  }
  // Le serveur vient peut-être de répondre (calcul de l'ordre) : ses étapes
  // valent pour l'ordre affiché, inutile de le redemander.
  showApproxResult(approxData || await requestServer(stops, false), stops, !!refine);
}

function onOrderChanged() {
  renumber();
  save();
  if (!computed) return;
  setStatus("Mise à jour de l'itinéraire…");
  refreshRoute().catch((e) => setStatus(e.message, true));
}

// Adresse modifiée : l'itinéraire affiché ne correspond plus à la liste.
function onAddressChanged() {
  save();
  renumber();
  if (!computed) return;
  clearResult();
  setStatus("Liste modifiée : relancez le calcul de l'itinéraire.");
}

// -------------------------------------------------------- liens et copie
function updateMapsLink() {
  const link = mapsLink;
  const points = addresses().map(forGoogle);
  const params = new URLSearchParams({
    api: "1",
    travelmode: "driving",
    origin: points[0],
    destination: points[points.length - 1],
  });
  const waypoints = points.slice(1, -1).slice(0, MAPS_URL_WAYPOINTS);
  if (waypoints.length) params.set("waypoints", waypoints.join("|"));
  link.href = "https://www.google.com/maps/dir/?" + params.toString();
  const dropped = points.length - 2 - waypoints.length;
  link.title = dropped > 0
    ? `Un lien Google Maps ne porte que ${MAPS_URL_WAYPOINTS} étapes : les ${dropped} dernières n'y seront pas.`
    : "Ouvrir cet itinéraire dans Google Maps (navigation)";
}

// Arrêts envoyés au formulaire de création d'un ordre de mission. Remplis au
// moment de la soumission et non au calcul : entre les deux, un arrêt a pu
// être déplacé, renommé ou supprimé — c'est la liste affichée qui fait foi.
function fillOmForm() {
  const form = document.getElementById("tour-om-form");
  form.textContent = "";
  filledRows().forEach((row) => {
    form.appendChild(hiddenField("tour_address[]", addressOf(row)));
    form.appendChild(hiddenField("tour_time[]", timeOf(row)));
  });
}

function hiddenField(name, value) {
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = name;
  input.value = value;
  return input;
}

function copyList() {
  const status = document.getElementById("tour-copy-status");
  const text = filledRows().map((row, index) => `${index + 1}. ${timeOf(row) ? timeOf(row) + " — " : ""}${addressOf(row)}`).join("\n");
  navigator.clipboard.writeText(text).then(
    () => { status.textContent = "Liste copiée."; },
    () => { status.textContent = "Copie refusée par le navigateur."; }
  );
  setTimeout(() => { status.textContent = ""; }, 2500);
}

// --------------------------------------------------------- glisser-déposer
// Événements *pointer*, et non l'API drag-and-drop HTML5 : celle-ci ne se
// déclenche pas au doigt, et l'écran doit marcher sur téléphone. Le geste
// réordonne le DOM en direct — la ligne suit donc le doigt d'elle-même,
// sans copie fantôme à déplacer.
function startDrag(grip, event) {
  const row = grip.closest("li");
  const from = rows().indexOf(row);
  // Sans cela, le navigateur sélectionne le texte alentour pendant le glissé.
  event.preventDefault();
  row.classList.add("is-dragging");
  // Le pointeur est capturé par la liste, et non par la poignée : déplacer la
  // ligne dans le DOM équivaut à la retirer puis la réinsérer, ce qui
  // relâcherait la capture de la poignée — le geste s'arrêterait au premier
  // déplacement. La liste, elle, ne bouge pas.
  listEl.setPointerCapture(event.pointerId);

  function onMove(moveEvent) {
    const y = moveEvent.clientY;
    // Première ligne dont on n'a pas atteint le milieu : c'est devant elle que
    // la ligne glissée vient se placer (à la fin si on est sous toutes).
    const target = rows().find((other) => {
      if (other === row) return false;
      const box = other.getBoundingClientRect();
      return y < box.top + box.height / 2;
    });
    // Rien à faire si la place ne change pas : une réinsertion inutile ferait
    // clignoter la liste à chaque mouvement du pointeur.
    if (target === row.nextElementSibling || (!target && !row.nextElementSibling)) return;
    listEl.insertBefore(row, target || null);
    renumber();
  }

  function onEnd() {
    listEl.removeEventListener("pointermove", onMove);
    listEl.removeEventListener("pointerup", onEnd);
    listEl.removeEventListener("pointercancel", onEnd);
    listEl.removeEventListener("lostpointercapture", onEnd);
    row.classList.remove("is-dragging");
    if (rows().indexOf(row) !== from) onOrderChanged();
  }

  listEl.addEventListener("pointermove", onMove);
  listEl.addEventListener("pointerup", onEnd);
  listEl.addEventListener("pointercancel", onEnd);
  // Filet : si la capture est perdue autrement (fenêtre qui perd le focus,
  // geste interrompu par le système), le glissé se termine proprement.
  listEl.addEventListener("lostpointercapture", onEnd);
}

// ----------------------------------------------- autocomplétion d'adresse
// Même recherche que la fiche client (address_autocomplete.js) : Google
// Places ou Base Adresse Nationale, selon le réglage global. Ici seul le
// libellé complet sert — une ligne, une adresse.
function attachSuggestions(row) {
  const input = row.querySelector(".tour-stop__address");
  KentAddress.attach(input, {
    provider: () => page.dataset.addressProvider,
    onPick: (item) => {
      input.value = item.label;
      onAddressChanged();
    },
  });
}

// ----------------------------------------------------------- mémorisation
// La liste est gardée par le navigateur (donc par appareil) : on revient sur
// l'écran, la tournée y est encore. C'est un brouillon de travail, rien qui
// mérite une table en base — et rien de sensible.
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        addresses: rows().map(addressOf),
        times: rows().map(timeOf),
        // Quelles heures viennent du calcul : sans cela, au rechargement,
        // la première heure venue passerait pour celle qu'on a saisie.
        computed: rows().map((row) => !!row.querySelector(".tour-stop__time").dataset.auto),
      }));
    } catch (e) { /* navigation privée, stockage bloqué : tant pis */ }
  }, 400);
}

function restore() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch (e) { saved = null; }
  const kept = saved && Array.isArray(saved.addresses) ? saved.addresses.slice(0, MAX_STOPS) : [];
  const times = (saved && Array.isArray(saved.times) ? saved.times : []).slice(0, MAX_STOPS);
  const fromCalc = (saved && Array.isArray(saved.computed) ? saved.computed : []).slice(0, MAX_STOPS);
  // Trois lignes vides pour commencer : de quoi voir tout de suite de quoi
  // il retourne (départ + deux arrêts).
  (kept.length ? kept : ["", "", ""]).forEach((address, index) => {
    const row = addRow(address);
    if (row && times[index]) {
      const field = row.querySelector(".tour-stop__time");
      const minutes = parseTime(times[index]);
      field.value = minutes == null ? times[index] : formatTime(minutes);
      if (fromCalc[index]) field.dataset.auto = "1";
    }
  });
}

function clearAll() {
  if (!confirm("Vider la liste des adresses ?")) return;
  rows().forEach((row) => row.remove());
  ["", "", ""].forEach((value) => addRow(value));
  knownCoords.clear();
  anchorRow = null;
  clearResult();
  setStatus("");
  mapNote.textContent = "";
  try { localStorage.removeItem(STORAGE_KEY); } catch (e) { /* tant pis */ }
}

// ------------------------------------------------------------------- init
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initRouteProvider();
  restore();

  document.getElementById("tour-add").addEventListener("click", () => {
    const row = addRow("");
    if (row) row.querySelector(".tour-stop__address").focus();
  });
  document.getElementById("tour-add-depot").addEventListener("click", () => {
    if (addRow(page.dataset.depotAddress)) onAddressChanged();
  });
  document.getElementById("tour-optimize").addEventListener("click", optimize);
  document.getElementById("tour-trace").addEventListener("click", traceCurrentOrder);
  document.getElementById("tour-clear").addEventListener("click", clearAll);
  document.getElementById("tour-copy").addEventListener("click", copyList);
  // submit et non click : la soumission passe par le formulaire, y compris
  // au clavier (Entrée sur le bouton).
  document.getElementById("tour-om-form").addEventListener("submit", fillOmForm);

  // Délégation : les lignes apparaissent et disparaissent au fil de la saisie.
  listEl.addEventListener("click", (e) => {
    const row = e.target.closest("li");
    if (!row) return;
    if (e.target.matches(".row-remove")) removeRow(row);
    else if (e.target.matches(".row-up")) moveRow(row, -1);
    else if (e.target.matches(".row-down")) moveRow(row, 1);
  });

  listEl.addEventListener("pointerdown", (e) => {
    const grip = e.target.closest(".tour-stop__grip");
    if (grip) startDrag(grip, e);
  });

  // Heure saisie à la main : elle devient la référence, et le prochain
  // calcul (ou le prochain glisser-déposer) recale les autres autour d'elle.
  listEl.addEventListener("input", (e) => {
    if (!e.target.matches(".tour-stop__time")) return;
    delete e.target.dataset.auto;
    anchorRow = e.target.closest("li");
    save();
  });

  // Sortie du champ : « 630 » devient « 06:30 ». Une saisie illisible reste
  // telle quelle, bien visible.
  listEl.addEventListener("blur", (e) => {
    if (!e.target.matches(".tour-stop__time")) return;
    const minutes = parseTime(e.target.value);
    if (minutes != null) e.target.value = formatTime(minutes);
    save();
  }, true);

  listEl.addEventListener("input", (e) => {
    if (e.target.matches(".tour-stop__address")) onAddressChanged();
  });

  // Entrée dans un champ d'adresse : passer à la ligne suivante (et la créer
  // au besoin) plutôt que de soumettre — la page n'a pas de formulaire.
  listEl.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target.matches(".tour-stop__address")) return;
    e.preventDefault();
    KentAddress.close();
    const next = e.target.closest("li").nextElementSibling || addRow("");
    if (next) next.querySelector(".tour-stop__address").focus();
  });
});

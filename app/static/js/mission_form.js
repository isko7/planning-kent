// Gestion des lignes dynamiques du formulaire "Ordre de mission" :
// tableau des trajets (OM) et tableau des arrêts (BC), + un bouton
// pratique pour pré-remplir les trajets à partir des arrêts saisis.

const DEPOT = "Dépôt KENT";
const ARROW = " → ";

function addRow(tableBodyId, templateId) {
  const tbody = document.getElementById(tableBodyId);
  const tpl = document.getElementById(templateId);
  tbody.appendChild(tpl.content.cloneNode(true));
}

function removeRow(button) {
  const tr = button.closest("tr");
  const rd = tr.querySelector(".relay-driver");
  if (rd) { rd.value = ""; syncRelayRemarks(tr, ""); }
  tr.parentNode.removeChild(tr);
}

// Déplace une ligne d'un cran vers le haut (dir=-1) ou le bas (dir=1).
// L'ordre du DOM = l'ordre enregistré (le back-end lit les champs dans
// l'ordre des lignes), donc rien à faire côté serveur.
function moveRow(button, dir) {
  const tr = button.closest("tr");
  if (dir < 0 && tr.previousElementSibling) {
    tr.parentNode.insertBefore(tr, tr.previousElementSibling);
  } else if (dir > 0 && tr.nextElementSibling) {
    tr.parentNode.insertBefore(tr.nextElementSibling, tr);
  }
}

function fillLegRow(tr, start, end, vehicleId, label) {
  tr.querySelector('[name="leg_start_time[]"]').value = start || "";
  tr.querySelector('[name="leg_end_time[]"]').value = end || "";
  const vSel = tr.querySelector('[name="leg_vehicle_id[]"]');
  if (vSel) vSel.value = vehicleId || "";
  tr.querySelector('[name="leg_label[]"]').value = label || "";
}

function defaultVehicleValue() {
  const sel = document.getElementById("default-vehicle-select");
  return sel ? sel.value : "";
}

function applyVehicleToAllLegs() {
  const v = defaultVehicleValue();
  if (!v) {
    alert("Choisissez d'abord un véhicule dans « Véhicule par défaut ».");
    return;
  }
  document.querySelectorAll('#legs-body select[name="leg_vehicle_id[]"]').forEach((sel) => {
    if (sel.value !== "relais") { sel.value = v; toggleRelayDriver(sel); }
  });
}

// ------------------------------------------------------------ relais
function relayText(option) {
  if (!option || !option.value) return "";
  const fn = (option.dataset.fn || "").trim();
  const ln = (option.dataset.ln || "").trim();
  const tel = (option.dataset.tel || "").trim();
  const who = [fn, ln].filter(Boolean).join(" ");
  return "Relais avec " + who + (tel ? " (" + tel + ")" : "");
}

// Affiche / masque le sélecteur de chauffeur de relais selon le véhicule.
function toggleRelayDriver(vehicleSel) {
  const tr = vehicleSel.closest("tr");
  const rd = tr.querySelector(".relay-driver");
  if (!rd) return;
  if (vehicleSel.value === "relais") {
    rd.hidden = false;
  } else {
    rd.hidden = true;
    if (rd.value) { rd.value = ""; onRelayDriverChange(rd); }
  }
}

// Chauffeur de relais choisi : renseigne le libellé du trajet et ajoute
// une ligne aux remarques (en remplaçant la précédente pour cette ligne).
function onRelayDriverChange(rd) {
  const tr = rd.closest("tr");
  const labelInput = tr.querySelector('[name="leg_label[]"]');
  const text = rd.value ? relayText(rd.selectedOptions[0]) : "";
  const prev = tr.dataset.relayText || "";

  if (text) {
    labelInput.value = text;
  } else if (labelInput.value === prev) {
    labelInput.value = "";
  }
  syncRelayRemarks(tr, text, prev);
  tr.dataset.relayText = text;
}

function syncRelayRemarks(tr, text, prev) {
  const remarks = document.querySelector('[name="remarks"]');
  if (!remarks) return;
  prev = prev !== undefined ? prev : (tr.dataset.relayText || "");
  let lines = remarks.value.split("\n");
  if (prev) lines = lines.filter((l) => l.trim() !== prev.trim());
  if (text && !lines.some((l) => l.trim() === text.trim())) lines.push(text);
  remarks.value = lines.join("\n").replace(/\n{3,}/g, "\n\n").replace(/^\n+|\n+$/g, "");
}

// ------------------------------------------- autocomplétion d'adresse
// Deux fournisseurs, choisis par le réglage global de l'écran Réglages
// (/reglages), lu depuis #mission-form[data-address-provider] :
// - "google" (par défaut) : Google Places, via la clé Maps JavaScript API
//   chargée en page (voir mission_form.html). Se replie automatiquement
//   sur la BAN si le script Google n'est pas chargé (clé absente).
// - "gouv" : Base Adresse Nationale, gratuite et sans clé — et surtout
//   elle renvoie la voie et la commune séparément, ce qui permet de
//   remplir « Adresse » et « Ville » d'un seul clic.
const BAN_URL = "https://api-adresse.data.gouv.fr/search/";
let googlePlacesService = null;

function closeSuggestions() {
  document.querySelectorAll(".addr-suggestions").forEach((el) => el.remove());
}

function addressProvider() {
  const form = document.getElementById("mission-form");
  return form ? form.dataset.addressProvider : "gouv";
}

function googleAvailable() {
  return !!(window.google && google.maps && google.maps.places);
}

function googlePlacePredictions(query) {
  return new Promise((resolve) => {
    if (!googleAvailable()) { resolve([]); return; }
    if (!googlePlacesService) googlePlacesService = new google.maps.places.AutocompleteService();
    googlePlacesService.getPlacePredictions(
      { input: query, componentRestrictions: { country: "fr" }, language: "fr" },
      (predictions) => resolve(predictions || [])
    );
  });
}

// Précision d'une suggestion Google, dans les termes de la BAN.
function googleKind(types) {
  if (types.includes("street_address") || types.includes("premise")) return "housenumber";
  if (types.includes("route")) return "street";
  if (types.includes("locality") || types.includes("postal_code")) return "municipality";
  return "place";
}

// Renvoie une liste uniforme {label, name, city, kind}, quel que soit le
// fournisseur — c'est ce que consomment la boîte de suggestions et la
// vérification des adresses lues (stops_ocr.js). kind : précision du
// résultat (housenumber, street, municipality…, termes de la BAN).
async function fetchAddressSuggestions(query) {
  if (addressProvider() === "google" && googleAvailable()) {
    const predictions = await googlePlacePredictions(query);
    return predictions.slice(0, 5).map((p) => {
      const sf = p.structured_formatting || {};
      const kind = googleKind(p.types || []);
      // « 62 Rue …  |  Lucé, France » ; pour une commune, son nom est le titre.
      const city = kind === "municipality" ? (sf.main_text || "")
        : (sf.secondary_text || "").split(",")[0].trim();
      return { label: p.description, name: sf.main_text || p.description, city, kind };
    });
  }
  try {
    const resp = await fetch(BAN_URL + "?" + new URLSearchParams({ q: query, limit: "5" }));
    const features = (await resp.json()).features || [];
    return features.map((f) => ({
      label: f.properties.label,
      name: f.properties.name || f.properties.label,
      city: f.properties.city || "",
      kind: f.properties.type,
    }));
  } catch (e) {
    return []; // hors ligne / API indisponible : on laisse la saisie libre
  }
}

async function showAddressSuggestions(input) {
  const q = input.value.trim();
  closeSuggestions();
  if (q.length < 3) return;

  const items = await fetchAddressSuggestions(q);
  if (!items.length || document.activeElement !== input) return;

  const box = document.createElement("div");
  box.className = "addr-suggestions";
  items.forEach((it) => {
    const item = document.createElement("div");
    item.className = "addr-suggestion";
    item.textContent = it.label;
    // mousedown plutôt que click : se déclenche avant le blur de l'input.
    item.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const tr = input.closest("tr");
      input.value = it.name;
      const cityInput = tr && tr.querySelector('[name="stop_city[]"]');
      if (cityInput) cityInput.value = it.city || "";
      closeSuggestions();
    });
    box.appendChild(item);
  });
  input.parentNode.appendChild(box);
}

// ------------------------------------------------ estimation de durée
// 2 boutons indépendants par ligne de trajet, chacun son fournisseur —
// TomTom (côté serveur, clé jamais exposée) et Google Maps (côté
// navigateur, via la clé Maps JavaScript API). Affichage seul dans les
// deux cas : les heures saisies (leg_start_time/leg_end_time), donc
// l'amplitude / la conduite / la pause du récap, ne sont jamais modifiées
// par un clic sur « Estimer ».
const DEPOT_FOLD = "depot kent";

function foldPlace(text) {
  return (text || "").trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

// Même règle que routing.normalize_place() côté serveur : « Dépôt KENT »
// n'est pas une adresse géocodable, on lui substitue celle de l'entreprise.
function normalizePlaceForGoogle(text) {
  const place = (text || "").trim();
  if (foldPlace(place) === DEPOT_FOLD) {
    const form = document.getElementById("mission-form");
    return (form && form.dataset.depotAddress) || place;
  }
  return place;
}

function splitLegLabel(tr, result) {
  const label = tr.querySelector('[name="leg_label[]"]').value;
  const parts = label.split(ARROW);
  if (parts.length !== 2 || !parts[0].trim() || !parts[1].trim()) {
    result.textContent = "Libellé attendu : « départ → arrivée »";
    result.className = "estimate-result estimate-result--error";
    return null;
  }
  return [parts[0].trim(), parts[1].trim()];
}

function formatDurationJs(seconds) {
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
}

function addMinutesToHHMM(startMinutes, deltaSeconds) {
  const total = startMinutes + Math.round(deltaSeconds / 60);
  const norm = ((total % 1440) + 1440) % 1440;
  return `${String(Math.floor(norm / 60)).padStart(2, "0")}:${String(norm % 60).padStart(2, "0")}`;
}

// Date de départ pour le calcul de trafic : celle saisie si elle est dans
// le futur (mission_date + start_time, ou end_time à défaut), sinon "now"
// (trafic courant) — même repli que _datetime_param() côté serveur.
function computeDepartureDate(missionDate, time) {
  const now = new Date();
  const mins = parseHHMM(time);
  if (!missionDate || mins == null) return { date: now, scheduled: false };
  const d = new Date(missionDate + "T00:00:00");
  d.setMinutes(d.getMinutes() + mins);
  return d > now ? { date: d, scheduled: true } : { date: now, scheduled: false };
}

async function estimateLegTomtom(button, tr, result) {
  const parts = splitLegLabel(tr, result);
  if (!parts) return;
  const form = document.getElementById("mission-form");

  const body = new FormData();
  body.append("from", parts[0]);
  body.append("to", parts[1]);
  body.append("start_time", tr.querySelector('[name="leg_start_time[]"]').value.trim());
  body.append("end_time", tr.querySelector('[name="leg_end_time[]"]').value.trim());
  const missionDate = document.querySelector('[name="mission_date"]');
  body.append("mission_date", missionDate ? missionDate.value : "");

  result.className = "estimate-result";
  result.textContent = "Calcul…";
  button.disabled = true;
  try {
    const resp = await fetch(form.dataset.estimateUrl, { method: "POST", body });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      result.textContent = data.error || "Estimation indisponible.";
      result.className = "estimate-result estimate-result--error";
      return;
    }
    let text = `TomTom ≈ ${data.duration}`;
    if (data.arrival_time) text += ` (arrivée estimée ${data.arrival_time})`;
    else if (data.departure_time) text += ` (départ estimé ${data.departure_time})`;
    text += ` · ${data.km} km`;
    if (data.traffic_min > 0) text += ` (dont ${data.traffic_min} min de trafic)`;
    if (!data.with_traffic_at) text += " · trafic actuel";
    result.textContent = text;
    setMeters(tr, data.meters != null ? data.meters : data.km * 1000);
    const info = legKey(tr);
    if (info) tr.dataset.estKey = info.key;
  } catch (e) {
    result.textContent = "Estimation indisponible : " + e.message;
    result.className = "estimate-result estimate-result--error";
  } finally {
    button.disabled = false;
    scheduleLegsSummaryUpdate();
  }
}

function estimateLegGoogle(button, tr, result) {
  const parts = splitLegLabel(tr, result);
  if (!parts) return;
  if (!googleAvailable() || !google.maps.DistanceMatrixService) {
    result.textContent = "Clé Google Maps absente : renseignez GOOGLE_MAPS_API_KEY.";
    result.className = "estimate-result estimate-result--error";
    return;
  }

  const origin = normalizePlaceForGoogle(parts[0]);
  const destination = normalizePlaceForGoogle(parts[1]);
  const startTime = tr.querySelector('[name="leg_start_time[]"]').value.trim();
  const endTime = tr.querySelector('[name="leg_end_time[]"]').value.trim();
  const missionDateInput = document.querySelector('[name="mission_date"]');
  const missionDate = missionDateInput ? missionDateInput.value : "";
  const { date: departure, scheduled } = computeDepartureDate(missionDate, startTime || endTime);

  result.className = "estimate-result";
  result.textContent = "Calcul…";
  button.disabled = true;
  new google.maps.DistanceMatrixService().getDistanceMatrix(
    {
      origins: [origin],
      destinations: [destination],
      travelMode: google.maps.TravelMode.DRIVING,
      drivingOptions: { departureTime: departure, trafficModel: google.maps.TrafficModel.BEST_GUESS },
      unitSystem: google.maps.UnitSystem.METRIC,
    },
    (response, status) => {
      button.disabled = false;
      if (status !== "OK") {
        result.textContent = "Estimation indisponible (" + status + ").";
        result.className = "estimate-result estimate-result--error";
        return;
      }
      const el = response.rows[0] && response.rows[0].elements[0];
      if (!el || el.status !== "OK") {
        result.textContent = "Itinéraire introuvable.";
        result.className = "estimate-result estimate-result--error";
        return;
      }
      const durationInfo = el.duration_in_traffic || el.duration;
      const km = Math.round(el.distance.value / 1000);
      const startMinutes = parseHHMM(startTime), endMinutes = parseHHMM(endTime);
      let text = `Maps ≈ ${formatDurationJs(durationInfo.value)}`;
      if (startMinutes != null) text += ` (arrivée estimée ${addMinutesToHHMM(startMinutes, durationInfo.value)})`;
      else if (endMinutes != null) text += ` (départ estimé ${addMinutesToHHMM(endMinutes, -durationInfo.value)})`;
      text += ` · ${km} km`;
      if (el.duration_in_traffic) {
        const trafficMin = Math.round((el.duration_in_traffic.value - el.duration.value) / 60);
        if (trafficMin > 0) text += ` (dont ${trafficMin} min de trafic)`;
      }
      if (!scheduled) text += " · trafic actuel";
      result.textContent = text;
      setMeters(tr, el.distance.value);
      const info = legKey(tr);
      if (info) tr.dataset.estKey = info.key;
      scheduleLegsSummaryUpdate();
    }
  );
}

function estimateLeg(button) {
  const tr = button.closest("tr");
  const result = tr.querySelector(".estimate-result");
  if (button.dataset.provider === "google") estimateLegGoogle(button, tr, result);
  else estimateLegTomtom(button, tr, result);
}

// -------------------------------------------- récap Trajets (km / temps)
// Heures de conduite + amplitude : calculées tout de suite depuis les
// heures déjà saisies. Kilomètres : pas stockés en base (seule la mini
// estimation par ligne les connaît), donc on interroge l'API d'estimation
// pour chaque ligne « départ → arrivée » valide, avec un petit cache par
// ligne (tr.dataset.estKey/estKm) pour ne pas re-appeler à chaque frappe.
function parseHHMM(value) {
  // Tolère 'h' en plus de ':' : le champ est du texte libre, et certaines
  // heures sont saisies "11h00" (format affiché partout ailleurs) plutôt
  // que "11:00" (ce que routing.js/add_minutes tolèrent déjà côté serveur).
  const m = (value || "").trim().match(/^(\d{1,2})\s*[:hH]\s*(\d{2})$/);
  return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : null;
}

function formatHoursMinutes(minutes) {
  if (minutes == null || isNaN(minutes)) return "—";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return `${h}h${String(m).padStart(2, "0")}`;
}

// Ecart entre 2 heures-du-jour en minutes, modulo 24h : gère les missions de
// nuit qui passent minuit (ex. 19h00 -> 02h30 = 7h30, pas -16h30). Même
// règle que legs_time_summary() côté serveur (utils.py).
function minutesBetween(startMinutes, endMinutes) {
  return ((endMinutes - startMinutes) % 1440 + 1440) % 1440;
}

function updateLegsTimeSummary() {
  // Amplitude = 1re heure de début valide -> dernière heure de fin valide,
  // dans l'ordre des lignes (comme service_time_range() côté serveur) —
  // pas un min/max numérique, qui se trompe dès qu'une mission passe minuit.
  let firstStart = null, lastEnd = null, drivingMinutes = 0;
  document.querySelectorAll("#legs-body tr").forEach((tr) => {
    const start = parseHHMM(tr.querySelector('[name="leg_start_time[]"]').value);
    const end = parseHHMM(tr.querySelector('[name="leg_end_time[]"]').value);
    if (start != null && end != null) {
      if (firstStart == null) firstStart = start;
      lastEnd = end;
    }
    const vSel = tr.querySelector('[name="leg_vehicle_id[]"]');
    const isDriving = vSel && vSel.value && vSel.value !== "relais";
    if (isDriving && start != null && end != null) {
      drivingMinutes += minutesBetween(start, end);
    }
  });
  const amplitude = (firstStart != null && lastEnd != null) ? minutesBetween(firstStart, lastEnd) : null;
  // Pause = amplitude - conduite : le reste du temps de service qui n'est
  // pas passé à conduire (attente, relais...), pas une saisie séparée.
  const pause = amplitude != null ? Math.max(0, amplitude - drivingMinutes) : null;
  const drivingEl = document.getElementById("legs-summary-driving");
  const pauseEl = document.getElementById("legs-summary-pause");
  const amplitudeEl = document.getElementById("legs-summary-amplitude");
  if (drivingEl) drivingEl.textContent = formatHoursMinutes(drivingMinutes);
  if (pauseEl) pauseEl.textContent = formatHoursMinutes(pause);
  if (amplitudeEl) amplitudeEl.textContent = formatHoursMinutes(amplitude);
}

// Distance d'un trajet, en mètres, dans un champ caché enregistré avec la
// mission : la fiche peut ainsi afficher les distances sans rappeler le
// service d'itinéraire à chaque consultation.
function legDistanceInput(tr) {
  return tr.querySelector('[name="leg_distance_m[]"]');
}

function storedMeters(tr) {
  const input = legDistanceInput(tr);
  const value = input && input.value.trim();
  return value ? Number(value) : null;
}

function setMeters(tr, meters) {
  const input = legDistanceInput(tr);
  if (input) input.value = meters == null ? "" : String(Math.round(meters));
}

function legKey(tr) {
  const label = tr.querySelector('[name="leg_label[]"]').value;
  const parts = label.split(ARROW);
  if (parts.length !== 2) return null;
  const from = parts[0].trim();
  const to = parts[1].trim();
  if (!from || !to) return null;
  const start = tr.querySelector('[name="leg_start_time[]"]').value.trim();
  const end = tr.querySelector('[name="leg_end_time[]"]').value.trim();
  return { from, to, key: `${from}|${to}|${start}|${end}`, start, end };
}

// La distance déjà enregistrée vaut pour le libellé affiché : on marque la
// ligne comme estimée, pour ne pas relancer tout le calcul à l'ouverture
// d'une mission qu'on n'a pas modifiée.
function adoptStoredDistances() {
  document.querySelectorAll("#legs-body tr").forEach((tr) => {
    const info = legKey(tr);
    if (info && storedMeters(tr) != null) tr.dataset.estKey = info.key;
  });
}

async function estimateLegKm(tr) {
  const info = legKey(tr);
  if (!info) return storedMeters(tr);
  // Déjà estimé pour ce libellé et ces horaires : rien à redemander.
  if (tr.dataset.estKey === info.key) return storedMeters(tr);

  const form = document.getElementById("mission-form");
  const missionDate = document.querySelector('[name="mission_date"]');
  const body = new FormData();
  body.append("from", info.from);
  body.append("to", info.to);
  body.append("start_time", info.start);
  body.append("end_time", info.end);
  body.append("mission_date", missionDate ? missionDate.value : "");

  let meters = null;
  try {
    const resp = await fetch(form.dataset.estimateUrl, { method: "POST", body });
    const data = await resp.json();
    if (resp.ok && data.ok) meters = data.meters != null ? data.meters : data.km * 1000;
  } catch (e) {
    meters = null;
  }
  // Le libellé a changé : la distance enregistrée ne lui correspond plus,
  // on l'efface même si l'estimation a échoué (mieux vaut « — » qu'un
  // chiffre qui ne veut plus rien dire).
  tr.dataset.estKey = info.key;
  setMeters(tr, meters);
  return meters;
}

// Trajet à vide : une extrémité est le dépôt (aller au premier point,
// retour du dernier). Même règle que utils.legs_distance_summary().
function isEmptyLeg(tr) {
  const info = legKey(tr);
  return !!info && (foldPlace(info.from) === DEPOT_FOLD || foldPlace(info.to) === DEPOT_FOLD);
}

function formatKm(meters) {
  return meters == null ? "—" : `${Math.round(meters / 1000)} km`;
}

let legsKmRequestToken = 0;
async function updateLegsKmTotal() {
  const token = ++legsKmRequestToken;
  const rows = Array.from(document.querySelectorAll("#legs-body tr"));
  const results = await Promise.all(rows.map(estimateLegKm));
  if (token !== legsKmRequestToken) return; // une saisie plus récente a relancé le calcul

  let total = 0, empty = 0, known = false;
  results.forEach((meters, i) => {
    if (meters == null) return;
    known = true;
    total += meters;
    if (isEmptyLeg(rows[i])) empty += meters;
  });

  const set = (id, meters) => {
    const el = document.getElementById(id);
    if (el) el.textContent = known ? formatKm(meters) : "—";
  };
  set("legs-summary-km", total);
  set("legs-summary-km-empty", empty);
  set("legs-summary-km-transport", total - empty);
}

let legsSummaryTimer = null;
function scheduleLegsSummaryUpdate() {
  updateLegsTimeSummary();
  clearTimeout(legsSummaryTimer);
  legsSummaryTimer = setTimeout(updateLegsKmTotal, 600);
}

// -------------------------------------- recherche d'adresse (dropdown)
// Réglage global (table app_settings), sauvegardé en AJAX dès le
// changement — pas besoin d'enregistrer toute la mission pour qu'il
// prenne effet. Met aussi à jour data-address-provider immédiatement,
// pour que l'autocomplétion des arrêts en tienne compte sans recharger.
function initAddressProviderToggle() {
  const select = document.getElementById("address-provider-select");
  const status = document.getElementById("address-provider-status");
  const form = document.getElementById("mission-form");
  if (!select || !form) return;

  // Carte repliée par défaut : son titre rappelle le fournisseur choisi.
  const preview = document.getElementById("address-provider-preview");
  const showPreview = () => {
    if (preview) preview.textContent = "— " + select.selectedOptions[0].textContent;
  };
  showPreview();

  select.addEventListener("change", async () => {
    showPreview();
    if (status) status.textContent = "Enregistrement…";
    try {
      const body = new FormData();
      body.append("address_search_provider", select.value);
      const resp = await fetch(form.dataset.addressProviderUrl, { method: "POST", body });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        if (status) status.textContent = data.error || "Échec de l'enregistrement.";
        return;
      }
      form.dataset.addressProvider = data.address_search_provider;
      if (status) {
        status.textContent = "Enregistré ✓";
        setTimeout(() => { status.textContent = ""; }, 2000);
      }
    } catch (e) {
      if (status) status.textContent = "Échec de l'enregistrement : " + e.message;
    }
  });
}

// ------------------------------------------------ création de client
// Crée un client sans quitter le formulaire de mission, puis l'ajoute au
// menu déroulant et le sélectionne.
// Coordonnées imprimées sur le Billet Collectif : recopiées de la fiche
// client à la sélection, puis modifiables pour cette mission seulement.
const BC_FIELDS = ["name", "address", "postal_code", "city", "phone"];

function bcClientInputs() {
  const out = {};
  BC_FIELDS.forEach((f) => {
    out[f] = document.querySelector(`[name="bc_client_${f}"]`);
  });
  return out;
}

function fillBcClientFrom(clientId) {
  const box = document.getElementById("bc-client");
  if (!box) return;
  let clients = {};
  try {
    clients = JSON.parse(box.dataset.clients || "{}");
  } catch (e) {
    return;
  }
  const client = clients[clientId] || null;
  const inputs = bcClientInputs();
  BC_FIELDS.forEach((f) => {
    if (inputs[f]) inputs[f].value = client ? (client[f] || "") : "";
  });
  updateBcClientPreview();
}

// Le cadre est replié par défaut : son titre rappelle le nom et la ville
// qui seront imprimés.
function updateBcClientPreview() {
  const preview = document.getElementById("bc-client-preview");
  if (!preview) return;
  const inputs = bcClientInputs();
  const parts = ["name", "city"].map((f) => inputs[f] && inputs[f].value.trim()).filter(Boolean);
  preview.textContent = parts.length ? "— " + parts.join(", ") : "— vide";
}

function registerClient(id, client) {
  const box = document.getElementById("bc-client");
  if (!box) return;
  let clients = {};
  try {
    clients = JSON.parse(box.dataset.clients || "{}");
  } catch (e) {
    clients = {};
  }
  clients[String(id)] = client;
  box.dataset.clients = JSON.stringify(clients);
}

function initBcClient() {
  const select = document.getElementById("client-select");
  const box = document.getElementById("bc-client");
  if (!select || !box) return;

  // Changer de client remplace les coordonnées : les retouches portaient
  // sur le client précédent, les garder n'aurait pas de sens.
  select.addEventListener("change", () => fillBcClientFrom(select.value));

  const reset = document.getElementById("bc-client-reset");
  if (reset) reset.addEventListener("click", () => fillBcClientFrom(select.value));

  // Mission déjà enregistrée avant l'arrivée de ces champs : on les amorce
  // depuis la fiche client plutôt que de laisser le cadre vide sur le BC.
  const inputs = bcClientInputs();
  const empty = BC_FIELDS.every((f) => inputs[f] && !inputs[f].value.trim());
  if (empty && select.value) fillBcClientFrom(select.value);

  box.addEventListener("input", updateBcClientPreview);
  updateBcClientPreview();
}

// Un <input type="date"> s'affiche selon la locale du navigateur (parfois
// mm/jj/aaaa). On montre à côté la date telle qu'elle sera imprimée.
function initEmissionDatePreview() {
  const input = document.getElementById("emission-date");
  const out = document.getElementById("emission-date-preview");
  if (!input || !out) return;
  const missionDate = document.querySelector('[name="mission_date"]');

  const render = () => {
    // Le BC retombe sur la date de mission quand l'émission est vide.
    const iso = input.value || (missionDate ? missionDate.value : "");
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    out.textContent = m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
    out.classList.toggle("is-fallback", !input.value && !!m);
  };
  input.addEventListener("change", render);
  input.addEventListener("input", render);
  if (missionDate) missionDate.addEventListener("change", render);
  render();
}

// Adresse du client créé à la volée : même recherche que la fiche client
// (KentAddress), qui remplit aussi le code postal et la ville.
function initNewClientAddress(fields) {
  if (!window.KentAddress || !fields.address) return;
  KentAddress.attach(fields.address, {
    provider: () => addressProvider(),
    onPick: (item) => item.details().then((full) => {
      fields.address.value = full.street || item.label;
      if (full.postcode) fields.postal_code.value = full.postcode;
      if (full.city) fields.city.value = full.city;
    }),
  });
}

// « Enregistrer et créer le retour » : la fenêtre demande la date et
// l'heure du premier arrêt du retour, puis le formulaire part avec — c'est
// ce bouton-là qui soumet, donc la vue sait qu'il faut enchaîner.
function initSaveAndReturn() {
  const button = document.getElementById("save-and-return");
  const form = document.getElementById("mission-form");
  if (!button || !form || !window.KentReturn) return;
  button.addEventListener("click", (e) => {
    if (button.dataset.ready) return;  // deuxième passage : on laisse partir
    e.preventDefault();
    const missionDate = form.querySelector('[name="mission_date"]');
    KentReturn.ask(missionDate && missionDate.value, (date, time) => {
      form.querySelector('[name="create_return_date"]').value = date;
      form.querySelector('[name="create_return_time"]').value = time;
      button.dataset.ready = "1";
      button.click();  // soumet vraiment, contrôles de saisie compris
    });
  });
}

function initNewClient() {
  const box = document.getElementById("new-client-box");
  const toggle = document.getElementById("new-client-toggle");
  const select = document.getElementById("client-select");
  if (!box || !toggle || !select) return;

  const msg = document.getElementById("nc-msg");
  const fields = {
    name: document.getElementById("nc-name"),
    address: document.getElementById("nc-address"),
    postal_code: document.getElementById("nc-postal-code"),
    city: document.getElementById("nc-city"),
    phone: document.getElementById("nc-phone"),
  };
  initNewClientAddress(fields);

  const close = () => {
    box.hidden = true;
    msg.textContent = "";
    Object.values(fields).forEach((f) => { f.value = ""; });
  };

  toggle.addEventListener("click", (e) => {
    e.preventDefault();
    box.hidden = !box.hidden;
    if (!box.hidden) fields.name.focus();
  });
  document.getElementById("nc-cancel").addEventListener("click", close);

  document.getElementById("nc-save").addEventListener("click", async () => {
    if (!fields.name.value.trim()) {
      msg.textContent = "Le nom du client est obligatoire.";
      fields.name.focus();
      return;
    }
    const body = new FormData();
    Object.entries(fields).forEach(([k, f]) => body.append(k, f.value.trim()));
    msg.textContent = "Création…";
    try {
      const resp = await fetch(box.dataset.url, { method: "POST", body });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        msg.textContent = data.error || "Échec de la création.";
        return;
      }
      const opt = document.createElement("option");
      opt.value = data.id;
      opt.textContent = data.name;
      select.appendChild(opt);
      select.value = data.id;
      // Le client vient d'être créé : il n'est pas dans la table rendue avec
      // la page. On l'y ajoute, puis on remplit le cadre du BC — affecter
      // select.value par programme ne déclenche aucun événement "change".
      registerClient(data.id, {
        name: data.name,
        address: fields.address ? fields.address.value.trim() : "",
        postal_code: fields.postal_code ? fields.postal_code.value.trim() : "",
        city: fields.city ? fields.city.value.trim() : "",
        phone: fields.phone ? fields.phone.value.trim() : "",
      });
      fillBcClientFrom(String(data.id));
      close();
    } catch (e) {
      msg.textContent = "Échec de la création : " + e.message;
    }
  });
}

// ---------------------------------------------------- génération legs
// Voyageurs des arrêts (Billet Collectif) : quand tous les voyageurs
// convergent vers un seul arrêt, celui-ci porte la somme des autres —
// y compris avec une seule prise en charge et une seule dépose, où la
// dépose reprend simplement le compte de la prise en charge.
// Même règle que côté serveur (app/utils.py:balance_passenger_counts), pour
// que le champ se mette à jour sous les yeux de la personne qui saisit.
function balancePassengerCounts() {
  const rows = Array.from(document.querySelectorAll("#stops-body tr"));
  const of = (tr) => ({
    type: tr.querySelector('[name="stop_type[]"]'),
    count: tr.querySelector('[name="stop_passenger_count[]"]'),
  });
  const cells = rows.map(of).filter((c) => c.type && c.count);
  const pickups = cells.filter((c) => c.type.value === "prise_en_charge");
  const dropoffs = cells.filter((c) => c.type.value === "depose");

  // On repart d'une ardoise propre : la configuration a pu changer.
  cells.forEach((c) => {
    c.count.readOnly = false;
    c.count.classList.remove("is-computed");
    c.count.removeAttribute("title");
  });

  let aggregated = null;
  let sources = null;
  if (dropoffs.length === 1 && pickups.length >= 1) {
    aggregated = dropoffs[0];
    sources = pickups;
  } else if (pickups.length === 1 && dropoffs.length >= 2) {
    aggregated = pickups[0];
    sources = dropoffs;
  } else {
    return; // N <-> N : rien d'évident à déduire, on laisse saisir.
  }

  const total = sources.reduce(
    (sum, c) => sum + (parseInt(c.count.value, 10) || 0), 0);
  aggregated.count.value = total;
  aggregated.count.readOnly = true;
  aggregated.count.classList.add("is-computed");
  aggregated.count.title = "Calculé : somme des autres arrêts.";
}

function generateLegsFromStops() {
  const stopRows = Array.from(document.querySelectorAll("#stops-body tr"));
  const stops = stopRows.map((tr) => ({
    time: tr.querySelector('[name="stop_time[]"]').value,
    address: tr.querySelector('[name="stop_address[]"]').value,
    city: tr.querySelector('[name="stop_city[]"]').value,
  })).filter((s) => s.address || s.city);

  if (stops.length === 0) {
    alert("Ajoutez d'abord au moins un arrêt (prise en charge / dépose).");
    return;
  }

  const legsBody = document.getElementById("legs-body");
  legsBody.innerHTML = "";
  const veh = defaultVehicleValue();
  // Ville puis adresse.
  const label = (s) => (s.city && s.address ? `${s.city}, ${s.address}` : (s.city || s.address));
  const first = stops[0];
  const last = stops[stops.length - 1];

  const add = (start, end, vehicleId, lbl) => {
    addRow("legs-body", "leg-row-template");
    fillLegRow(legsBody.lastElementChild, start, end, vehicleId, lbl);
  };

  // Prise / fin de service : heures laissées vides, c'est le chauffeur qui
  // les renseigne (elles ne se déduisent pas des arrêts).
  add("", "", veh, `Prise de service - ${DEPOT}`);
  add("", first.time, veh, `${DEPOT}${ARROW}${label(first)}`);
  for (let i = 0; i < stops.length - 1; i++) {
    add(stops[i].time, stops[i + 1].time, veh, `${label(stops[i])}${ARROW}${label(stops[i + 1])}`);
  }
  add(last.time, "", veh, `${label(last)}${ARROW}${DEPOT}`);
  add("", "", veh, `Fin de service - ${DEPOT}`);
}

// Répercute la date de la mission sur tous les arrêts (BC) existants,
// pour éviter d'avoir à la corriger ligne par ligne.
function syncStopDates(value) {
  document.querySelectorAll('#stops-body [name="stop_date[]"]').forEach((input) => {
    input.value = value;
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const missionDateInput = document.querySelector('[name="mission_date"]');
  if (missionDateInput) {
    missionDateInput.addEventListener("change", () => syncStopDates(missionDateInput.value));
  }

  const addLegBtn = document.getElementById("add-leg-row");
  if (addLegBtn) addLegBtn.addEventListener("click", () => {
    addRow("legs-body", "leg-row-template");
    scheduleLegsSummaryUpdate();
  });

  // Toute modification du tableau des arrêts (saisie, changement de type,
  // ajout ou suppression de ligne) peut changer l'arrêt qui agrège.
  const stopsBody = document.getElementById("stops-body");
  if (stopsBody) {
    ["input", "change"].forEach((evt) =>
      stopsBody.addEventListener(evt, balancePassengerCounts));
    new MutationObserver(balancePassengerCounts)
      .observe(stopsBody, { childList: true });
    balancePassengerCounts();
  }

  const addStopBtn = document.getElementById("add-stop-row");
  if (addStopBtn) addStopBtn.addEventListener("click", () => {
    addRow("stops-body", "stop-row-template");
    // La date du gabarit est figée au chargement de la page (souvent vide
    // sur une mission neuve) : on la reprend depuis le champ Date de la
    // mission au moment de l'ajout, pour avoir la valeur à jour.
    const tr = document.getElementById("stops-body").lastElementChild;
    const dateInput = tr && tr.querySelector('[name="stop_date[]"]');
    const missionDate = document.querySelector('[name="mission_date"]');
    if (dateInput && missionDate && missionDate.value) dateInput.value = missionDate.value;
  });

  const genBtn = document.getElementById("generate-legs-btn");
  if (genBtn) genBtn.addEventListener("click", () => {
    generateLegsFromStops();
    scheduleLegsSummaryUpdate();
  });

  const applyBtn = document.getElementById("apply-vehicle-all");
  if (applyBtn) applyBtn.addEventListener("click", () => {
    applyVehicleToAllLegs();
    scheduleLegsSummaryUpdate();
  });

  initNewClient();
  initSaveAndReturn();
  initBcClient();
  initEmissionDatePreview();
  initAddressProviderToggle();

  // Init : afficher les sélecteurs de relais déjà actifs et mémoriser
  // leur texte pour la synchro des remarques.
  document.querySelectorAll("#legs-body tr").forEach((tr) => {
    const v = tr.querySelector(".leg-vehicle");
    const rd = tr.querySelector(".relay-driver");
    if (v && v.value === "relais" && rd) {
      rd.hidden = false;
      if (rd.value) tr.dataset.relayText = relayText(rd.selectedOptions[0]);
    }
  });

  document.body.addEventListener("click", (e) => {
    if (e.target.matches(".row-remove")) {
      removeRow(e.target);
      scheduleLegsSummaryUpdate();
    } else if (e.target.matches(".row-up")) moveRow(e.target, -1);
    else if (e.target.matches(".row-down")) moveRow(e.target, 1);
    else if (e.target.matches(".estimate-leg")) estimateLeg(e.target);
    else if (!e.target.closest(".addr-suggestions")) closeSuggestions();
  });

  // Autocomplétion : délégation, pour couvrir aussi les lignes ajoutées
  // après le chargement de la page. `input` ne bulle pas sur `focusout`,
  // d'où les deux écouteurs en phase de capture.
  let addrTimer = null;
  document.body.addEventListener("input", (e) => {
    if (e.target.matches('[name="stop_address[]"]')) {
      clearTimeout(addrTimer);
      addrTimer = setTimeout(() => showAddressSuggestions(e.target), 250);
    } else if (e.target.matches(
      '#legs-body [name="leg_start_time[]"], #legs-body [name="leg_end_time[]"], #legs-body [name="leg_label[]"]'
    )) {
      scheduleLegsSummaryUpdate();
    }
  });
  document.body.addEventListener("focusout", (e) => {
    if (e.target.matches('[name="stop_address[]"]')) setTimeout(closeSuggestions, 150);
  });

  document.body.addEventListener("change", (e) => {
    if (e.target.matches(".leg-vehicle")) {
      toggleRelayDriver(e.target);
      scheduleLegsSummaryUpdate();
    } else if (e.target.matches(".relay-driver")) onRelayDriverChange(e.target);
  });

  // Récap Trajets à jour dès le chargement (missions existantes) : les
  // distances déjà enregistrées sont reprises telles quelles.
  adoptStoredDistances();
  scheduleLegsSummaryUpdate();
});

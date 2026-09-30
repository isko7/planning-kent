// Estimation de la durée et de la distance d'un trajet « départ → arrivée ».
// Partagée par le formulaire d'ordre de mission (un bouton par ligne de
// trajet) et la fiche (colonne Trajet, réservée aux administrateurs), pour
// que les deux écrans affichent exactement la même phrase.
//
// Deux fournisseurs, chacun pour ce qu'il fait de mieux — ce sont les deux
// boutons proposés côte à côte :
// - « tomtom » : appel au serveur (/missions/estimer-duree), qui garde la
//   clé et calcule le trafic pour l'heure de départ saisie ;
// - « google » : Distance Matrix, dans le navigateur, avec la clé Maps déjà
//   utilisée par l'autocomplétion des adresses et la carte du Plan de
//   Ramassage.
//
// Chaque fournisseur rend { text, meters } : la phrase à afficher telle
// quelle (« TomTom ≈ 1 h 05 (arrivée estimée 09:05) · 42 km ») et les
// mètres bruts — c'est ce que le formulaire enregistre dans son champ caché,
// pour que la somme de plusieurs trajets courts ne parte pas en arrondis.
// Une estimation impossible rejette avec une Error dont le message est
// affichable en l'état.
window.KentRouteEstimate = (function () {
  const ARROW = " → ";
  const DEPOT_FOLD = "depot kent";
  const MISSING_KEY = "Clé Google Maps absente : renseignez GOOGLE_MAPS_API_KEY.";

  // « départ → arrivée » -> ["départ", "arrivée"], null si le libellé n'est
  // pas un trajet (point de contrôle, pause, texte libre).
  function splitLabel(label) {
    const parts = (label || "").split(ARROW);
    if (parts.length !== 2 || !parts[0].trim() || !parts[1].trim()) return null;
    return [parts[0].trim(), parts[1].trim()];
  }

  // Tolère 'h' en plus de ':' : le champ est du texte libre, et certaines
  // heures sont saisies « 11h00 » (format affiché partout ailleurs) plutôt
  // que « 11:00 » — ce que add_minutes() tolère déjà côté serveur.
  function parseHHMM(value) {
    const m = (value || "").trim().match(/^(\d{1,2})\s*[:hH]\s*(\d{2})$/);
    return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : null;
  }

  function formatDuration(seconds) {
    const minutes = Math.round(seconds / 60);
    const h = Math.floor(minutes / 60), m = minutes % 60;
    return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
  }

  function addMinutes(startMinutes, deltaSeconds) {
    const total = startMinutes + Math.round(deltaSeconds / 60);
    const norm = ((total % 1440) + 1440) % 1440;
    return `${String(Math.floor(norm / 60)).padStart(2, "0")}:${String(norm % 60).padStart(2, "0")}`;
  }

  function foldPlace(text) {
    return (text || "").trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  // Même règle que routing.normalize_place() côté serveur : « Dépôt KENT »
  // n'est pas une adresse géocodable, on lui substitue celle de l'entreprise.
  function normalizeForGoogle(place, depotAddress) {
    const text = (place || "").trim();
    return foldPlace(text) === DEPOT_FOLD ? (depotAddress || text) : text;
  }

  // Date de départ pour le calcul de trafic : celle saisie si elle est dans
  // le futur (mission_date + heure), sinon « maintenant » (trafic courant)
  // — même repli que _datetime_param() côté serveur.
  function departureDate(missionDate, time) {
    const now = new Date();
    const mins = parseHHMM(time);
    if (!missionDate || mins == null) return { date: now, scheduled: false };
    const d = new Date(missionDate + "T00:00:00");
    d.setMinutes(d.getMinutes() + mins);
    return d > now ? { date: d, scheduled: true } : { date: now, scheduled: false };
  }

  async function tomtom(req) {
    const body = new FormData();
    body.append("from", req.from);
    body.append("to", req.to);
    body.append("start_time", req.startTime || "");
    body.append("end_time", req.endTime || "");
    body.append("mission_date", req.missionDate || "");

    let resp, data;
    try {
      resp = await fetch(req.estimateUrl, { method: "POST", body });
      data = await resp.json();
    } catch (e) {
      throw new Error("Estimation indisponible : " + e.message);
    }
    if (!resp.ok || !data.ok) throw new Error((data && data.error) || "Estimation indisponible.");

    let text = `TomTom ≈ ${data.duration}`;
    if (data.arrival_time) text += ` (arrivée estimée ${data.arrival_time})`;
    else if (data.departure_time) text += ` (départ estimé ${data.departure_time})`;
    text += ` · ${data.km} km`;
    if (data.traffic_min > 0) text += ` (dont ${data.traffic_min} min de trafic)`;
    if (!data.with_traffic_at) text += " · trafic actuel";
    return { text, meters: data.meters != null ? data.meters : data.km * 1000 };
  }

  function mapsReady() {
    return !!(window.google && google.maps && google.maps.DistanceMatrixService);
  }

  // Les écrans qui se servent de l'API Maps en continu (autocomplétion du
  // formulaire, carte du Plan de Ramassage) la chargent eux-mêmes ; la fiche,
  // elle, attend le premier clic sur « Maps » — c'est la page la plus ouverte
  // de l'application, et l'estimation y est occasionnelle. `key` n'est donc
  // utile qu'à ce chargement tardif.
  let loader = null;

  function loadGoogleMaps(key) {
    if (mapsReady()) return Promise.resolve(google.maps);
    if (!key) return Promise.reject(new Error(MISSING_KEY));
    if (!loader) {
      loader = new Promise(function (resolve, reject) {
        // Nom fixe : le rappel est lu par l'API dans window, et il n'y a
        // jamais qu'un chargement en vol.
        window.KentMapsLoaded = function () { resolve(google.maps); };
        const script = document.createElement("script");
        script.src = "https://maps.googleapis.com/maps/api/js?key=" +
          encodeURIComponent(key) + "&language=fr&region=FR&callback=KentMapsLoaded";
        script.onerror = function () {
          loader = null;  // le clic suivant pourra réessayer
          reject(new Error("Chargement de Google Maps impossible."));
        };
        document.head.appendChild(script);
      });
    }
    return loader;
  }

  function google_(req) {
    return loadGoogleMaps(req.googleMapsKey).then((maps) => new Promise((resolve, reject) => {
      const startMinutes = parseHHMM(req.startTime), endMinutes = parseHHMM(req.endTime);
      const { date: departure, scheduled } = departureDate(
        req.missionDate, req.startTime || req.endTime);
      new maps.DistanceMatrixService().getDistanceMatrix(
        {
          origins: [normalizeForGoogle(req.from, req.depotAddress)],
          destinations: [normalizeForGoogle(req.to, req.depotAddress)],
          travelMode: maps.TravelMode.DRIVING,
          drivingOptions: { departureTime: departure, trafficModel: maps.TrafficModel.BEST_GUESS },
          unitSystem: maps.UnitSystem.METRIC,
        },
        (response, status) => {
          if (status !== "OK") {
            reject(new Error("Estimation indisponible (" + status + ")."));
            return;
          }
          const el = response.rows[0] && response.rows[0].elements[0];
          if (!el || el.status !== "OK") {
            reject(new Error("Itinéraire introuvable."));
            return;
          }
          const durationInfo = el.duration_in_traffic || el.duration;
          let text = `Maps ≈ ${formatDuration(durationInfo.value)}`;
          if (startMinutes != null) text += ` (arrivée estimée ${addMinutes(startMinutes, durationInfo.value)})`;
          else if (endMinutes != null) text += ` (départ estimé ${addMinutes(endMinutes, -durationInfo.value)})`;
          text += ` · ${Math.round(el.distance.value / 1000)} km`;
          if (el.duration_in_traffic) {
            const trafficMin = Math.round((el.duration_in_traffic.value - el.duration.value) / 60);
            if (trafficMin > 0) text += ` (dont ${trafficMin} min de trafic)`;
          }
          if (!scheduled) text += " · trafic actuel";
          resolve({ text, meters: el.distance.value });
        }
      );
    }));
  }

  // `provider` vaut « google » ou « tomtom » (par défaut) : la valeur portée
  // par data-provider sur les deux boutons.
  function estimate(provider, req) {
    return provider === "google" ? google_(req) : tomtom(req);
  }

  return { ARROW, estimate, splitLabel, parseHHMM, formatDuration, addMinutes };
})();

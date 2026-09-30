// Liens Google Maps d'un itinéraire, construits dans le navigateur.
//
// Pendant de routing.build_maps_url() côté serveur : la fiche affiche des
// liens calculés une fois pour toutes au rendu, le formulaire doit les
// refaire à chaque frappe — le libellé d'un trajet change sous les doigts,
// et une ligne peut devenir (ou cesser d'être) un « départ → arrivée ». Les
// deux doivent donner le même lien pour les mêmes trajets : toute règle
// touchée ici l'est aussi là-bas.
window.KentMapsLinks = (function () {
  const ARROW = " → ";
  const DEPOT_FOLD = "depot kent";
  const BASE = "https://www.google.com/maps/dir/?";

  function fold(text) {
    return (text || "").trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  // Même règle que routing.normalize_place() : « Dépôt KENT » n'est pas une
  // adresse, il cède celle de l'entreprise ; et « VILLE, adresse » (l'ordre
  // d'affichage de l'OM) repasse dans l'ordre naturel « adresse, VILLE ».
  function normalizePlace(text, depotAddress) {
    const place = (text || "").trim();
    if (fold(place) === DEPOT_FOLD) return depotAddress || place;
    const cut = place.indexOf(", ");
    return cut > 0 ? `${place.slice(cut + 2)}, ${place.slice(0, cut)}` : place;
  }

  // « départ → arrivée » -> [départ, arrivée]. Comme leg_places() côté
  // serveur, on coupe au premier ↔ : un libellé qui en porte plusieurs reste
  // exploité de la même façon des deux côtés.
  function splitLabel(label) {
    const cut = (label || "").indexOf(ARROW);
    if (cut < 0) return null;
    const from = label.slice(0, cut).trim();
    const to = label.slice(cut + ARROW.length).trim();
    return from && to ? [from, to] : null;
  }

  // Lien passant par `places` dans l'ordre, les lieux intermédiaires en
  // waypoints. Le dépôt de départ y figure — c'est la variante « bureau »
  // de build_maps_url(), pas celle envoyée au chauffeur, qui l'omet au
  // profit de sa position courante. null s'il n'y a pas deux lieux.
  function dirUrl(places, depotAddress) {
    const kept = (places || []).filter((p) => (p || "").trim());
    if (kept.length < 2) return null;
    const normalized = kept.map((p) => normalizePlace(p, depotAddress));
    const params = new URLSearchParams();
    params.set("api", "1");
    params.set("travelmode", "driving");
    params.set("destination", normalized[normalized.length - 1]);
    params.set("origin", normalized[0]);
    const waypoints = normalized.slice(1, -1);
    if (waypoints.length) params.set("waypoints", waypoints.join("|"));
    return BASE + params.toString();
  }

  // Un seul trajet, depuis son libellé. null si ce n'en est pas un.
  function legUrl(label, depotAddress) {
    return dirUrl(splitLabel(label) || [], depotAddress);
  }

  // L'itinéraire entier, depuis les lignes du tableau : `legs` est une liste
  // de { label, driving }, `driving` disant si la ligne est un vrai trajet de
  // conduite (véhicule affecté, ni pause, ni relais, ni point de contrôle —
  // la règle de _driving_places() côté serveur, que l'appelant applique parce
  // qu'il est le seul à lire le formulaire). Un lieu n'est pas répété quand
  // il enchaîne deux trajets.
  function itineraryUrl(legs, depotAddress) {
    const places = [];
    (legs || []).forEach((leg) => {
      if (!leg || !leg.driving) return;
      const pair = splitLabel(leg.label);
      if (!pair) return;
      // Comparaison en minuscules seulement, comme _driving_places() : les
      // deux côtés doivent dédoublonner exactement les mêmes lieux.
      const dernier = places.length ? places[places.length - 1].toLowerCase() : null;
      if (dernier !== pair[0].toLowerCase()) places.push(pair[0]);
      places.push(pair[1]);
    });
    return dirUrl(places, depotAddress);
  }

  return { legUrl, itineraryUrl, normalizePlace, splitLabel };
})();

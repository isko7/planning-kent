"""
Écran Plan de Ramassage : une liste d'adresses, remise dans l'ordre qui raccourcit le
trajet, et la carte de l'itinéraire — indépendant des ordres de mission.

Rien n'est imposé : le calcul choisit aussi le premier et le dernier arrêt.
C'est pour cela qu'il commence ici et non dans le navigateur — un itinéraire
Google Maps veut un départ et une arrivée connus. Cette vue géocode donc les
adresses (Base Adresse Nationale, puis recherche TomTom) et les ordonne à vol
d'oiseau (`app/routing.py`) ; le navigateur demande ensuite à Google, s'il est
disponible, de réoptimiser le milieu du parcours sur les distances routières
réelles et d'en tracer la carte (voir `static/js/tours.js`).

Appelée avec `optimize=0`, elle ne réordonne rien : elle renvoie les
coordonnées et les distances de l'ordre reçu, pour redessiner la carte après
un glisser-déposer quand l'itinéraire routier de Google n'est pas disponible.
"""
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, render_template, request

from app import routing
from app.config import COMPANY, GOOGLE_MAPS_API_KEY, TOMTOM_API_KEY
from app.routes.settings import get_address_search_provider

bp = Blueprint("tours", __name__, url_prefix="/tournees")

# Plafond côté serveur : le solveur reste sous la seconde jusqu'à une
# quarantaine d'arrêts.
MAX_STOPS = 40

# Plafond de l'écran, plus bas : au-delà, Google Maps refuse l'itinéraire
# (25 points de passage au maximum). Repris par le gabarit et par tours.js.
BROWSER_MAX_STOPS = 25

# Géocodages menés de front : un aller-retour vers la BAN prend ~200 ms, les
# enchaîner rendrait une tournée de 25 arrêts inutilement longue à calculer.
GEOCODE_WORKERS = 8


@bp.route("/")
def planner_view():
    return render_template(
        "tours/planner.html",
        google_maps_api_key=GOOGLE_MAPS_API_KEY,
        address_search_provider=get_address_search_provider(),
        depot_address=f"{COMPANY['address']}, {COMPANY['postal_code']} {COMPANY['city']}",
        max_stops=BROWSER_MAX_STOPS,
        tomtom_enabled=bool(TOMTOM_API_KEY),
    )


def _geocode(address):
    """(lat, lon) ou (None, message) — l'erreur est renvoyée plutôt que levée
    pour pouvoir dire *quelle* adresse n'a pas été reconnue."""
    try:
        return routing.geocode_address(address), None
    except routing.RoutingError as e:
        return None, str(e)


@bp.route("/optimiser", methods=["POST"])
def optimize_tour():
    """Appelé en fetch depuis l'écran : `address` (répété) et `optimize`
    (« 0 » pour garder l'ordre reçu). Réponse en JSON.

    `order` est la liste des indices des adresses reçues, dans l'ordre de
    passage ; `points` suit l'ordre de la demande (le navigateur les garde en
    cache par adresse). Les distances sont à vol d'oiseau : elles servent à
    trancher entre deux ordres, pas à annoncer un kilométrage."""
    addresses = [a.strip() for a in request.form.getlist("address")]
    optimize = request.form.get("optimize") != "0"

    if len(addresses) < 2:
        return jsonify({"ok": False, "error": "Il faut au moins deux adresses."}), 400
    if len(addresses) > MAX_STOPS:
        return jsonify({"ok": False, "error": f"{MAX_STOPS} adresses au maximum."}), 400
    if not all(addresses):
        return jsonify({"ok": False, "error": "Une des adresses est vide."}), 400

    with ThreadPoolExecutor(max_workers=GEOCODE_WORKERS) as pool:
        geocoded = list(pool.map(_geocode, addresses))
    for index, (point, error) in enumerate(geocoded):
        if error:
            return jsonify({
                "ok": False,
                "failed_index": index,
                "error": f"Adresse introuvable : « {addresses[index]} » ({error}).",
            }), 400

    points = [point for point, _ in geocoded]
    order = (routing.shortest_tour_order(points) if optimize
             else list(range(len(points))))
    legs = routing.tour_legs_km([points[i] for i in order])
    return jsonify({
        "ok": True,
        "order": order,
        "points": [{"lat": lat, "lng": lon} for lat, lon in points],
        "legs_km": [round(km, 2) for km in legs],
        "total_km": round(sum(legs), 2),
    })


@bp.route("/navettes", methods=["POST"])
def plan_shuttles_view():
    """Section « Navettes » de l'écran, indépendante de l'itinéraire du
    haut : des points de ramassage avec leur nombre de voyageurs, une
    destination commune, et des navettes de N places.

    Appelé en fetch : `address` et `pax` répétés et appariés, `destination`,
    `seats`, `shuttles` (facultatif : nombre de navettes à ne pas dépasser).
    Répond en JSON — une navette par groupe, arrêts dans l'ordre de passage,
    distances à vol d'oiseau (le navigateur les refait par la route)."""
    addresses = [a.strip() for a in request.form.getlist("address")]
    counts = request.form.getlist("pax")
    destination = (request.form.get("destination") or "").strip()
    seats = request.form.get("seats", type=int) or 0
    limit = request.form.get("shuttles", type=int)

    if not destination:
        return jsonify({"ok": False, "error": "Renseignez la destination finale."}), 400
    if not addresses:
        return jsonify({"ok": False, "error": "Ajoutez au moins une adresse de ramassage."}), 400
    if len(addresses) > routing.MAX_SHUTTLE_STOPS:
        return jsonify({"ok": False,
                        "error": f"{routing.MAX_SHUTTLE_STOPS} adresses au maximum."}), 400
    if not all(addresses):
        return jsonify({"ok": False, "error": "Une des adresses est vide."}), 400
    demands = []
    for index, value in enumerate(counts[:len(addresses)]):
        try:
            demands.append(max(1, int(value)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "failed_index": index,
                            "error": "Nombre de voyageurs illisible."}), 400
    demands += [1] * (len(addresses) - len(demands))

    with ThreadPoolExecutor(max_workers=GEOCODE_WORKERS) as pool:
        geocoded = list(pool.map(_geocode, addresses + [destination]))
    for index, (point, error) in enumerate(geocoded):
        if error:
            which = destination if index == len(addresses) else addresses[index]
            return jsonify({
                "ok": False,
                "failed_index": None if index == len(addresses) else index,
                "error": f"Adresse introuvable : « {which} » ({error}).",
            }), 400

    points = [point for point, _ in geocoded[:-1]]
    target = geocoded[-1][0]
    try:
        groups = routing.plan_shuttles(points, demands, target, seats or 8, limit)
    except routing.RoutingError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    shuttles = []
    for group in groups:
        legs = [routing.haversine_km(points[group[i]], points[group[i + 1]])
                for i in range(len(group) - 1)]
        legs.append(routing.haversine_km(points[group[-1]], target))
        shuttles.append({
            "stops": group,
            "passengers": sum(demands[i] for i in group),
            "legs_km": [round(km, 2) for km in legs],
            "total_km": round(sum(legs), 2),
        })
    return jsonify({
        "ok": True,
        "seats": seats or 8,
        "points": [{"lat": lat, "lng": lon} for lat, lon in points],
        "destination": {"lat": target[0], "lng": target[1]},
        "shuttles": shuttles,
    })


@bp.route("/itineraire", methods=["POST"])
def route_view():
    """Itinéraire routier par TomTom, quand c'est lui qui est choisi plutôt
    que Google Maps (dont le navigateur se charge tout seul).

    `address` répété, dans l'ordre de passage. Réponse en JSON : une étape
    par tronçon (km, secondes), le tracé à dessiner, et les points géocodés
    pour poser les repères."""
    addresses = [a.strip() for a in request.form.getlist("address")]
    if len(addresses) < 2:
        return jsonify({"ok": False, "error": "Il faut au moins deux adresses."}), 400
    if len(addresses) > MAX_STOPS:
        return jsonify({"ok": False, "error": f"{MAX_STOPS} adresses au maximum."}), 400

    with ThreadPoolExecutor(max_workers=GEOCODE_WORKERS) as pool:
        geocoded = list(pool.map(_geocode, addresses))
    for index, (point, error) in enumerate(geocoded):
        if error:
            return jsonify({"ok": False, "failed_index": index,
                            "error": f"Adresse introuvable : « {addresses[index]} » ({error})."}), 400

    points = [point for point, _ in geocoded]
    try:
        route = routing.route_via_tomtom(points)
    except routing.RoutingError as e:
        return jsonify({"ok": False, "error": f"TomTom : {e}"}), 502
    return jsonify({
        "ok": True,
        "legs": route["legs"],
        "path": [{"lat": lat, "lng": lon} for lat, lon in route["path"]],
        "points": [{"lat": lat, "lng": lon} for lat, lon in points],
    })

// Fiche d'un ordre de mission, colonne Trajet : estimation de la durée et de
// la distance d'un trajet, à la demande. Le calcul est celui du formulaire
// (window.KentRouteEstimate, deux fournisseurs), à ceci près qu'ici il ne
// fait qu'afficher : rien n'est enregistré, la mission n'est pas en cours de
// saisie. Les distances du récap, elles, viennent de celles estimées au
// moment de la saisie (mission_legs.distance_m).
//
// Les liens « Ouvrir dans Maps » (par trajet et pour l'itinéraire complet)
// sont construits côté serveur : ce sont de simples liens, sans script.
//
// Tout est délégué au document, et la carte des trajets n'est rendue qu'aux
// administrateurs (missions/detail.html) : sans elle, ce script ne fait rien.
(function () {
  const card = document.getElementById("legs-card");
  if (!card || !window.KentRouteEstimate) return;

  function estimateLeg(button) {
    const row = button.closest("[data-leg-estimate]");
    const result = row && row.querySelector(".estimate-result");
    if (!result) return;

    const buttons = row.querySelectorAll(".estimate-leg");
    result.className = "estimate-result";
    result.textContent = "Calcul…";
    buttons.forEach(function (b) { b.disabled = true; });

    window.KentRouteEstimate.estimate(button.dataset.provider, {
      from: row.dataset.from,
      to: row.dataset.to,
      startTime: row.dataset.start || "",
      endTime: row.dataset.end || "",
      missionDate: card.dataset.missionDate || "",
      estimateUrl: card.dataset.estimateUrl,
      depotAddress: card.dataset.depotAddress,
      googleMapsKey: card.dataset.googleMapsKey,
    }).then(function (estimation) {
      result.textContent = estimation.text;
    }).catch(function (e) {
      result.textContent = e.message;
      result.className = "estimate-result estimate-result--error";
    }).finally(function () {
      buttons.forEach(function (b) { b.disabled = false; });
    });
  }

  card.addEventListener("click", function (e) {
    const button = e.target.closest(".estimate-leg");
    if (button) estimateLeg(button);
  });
})();

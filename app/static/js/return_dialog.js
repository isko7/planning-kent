// Fenêtre « Créer le retour » (missions/_return_dialog.html) : date et heure
// du premier arrêt du retour, avant de le créer.
//
// Deux appelants :
// - la fiche d'un ordre de mission, dont le formulaire « Créer le retour »
//   porte `data-return-form` : il est intercepté, la fenêtre s'ouvre, et ses
//   valeurs partent dans les champs cachés du formulaire ;
// - le formulaire d'ordre de mission (« Enregistrer et créer le retour »),
//   qui doit d'abord s'enregistrer : il appelle KentReturn.ask() lui-même.
window.KentReturn = (function () {
  const dialog = document.querySelector("[data-return-dialog]");
  const dateField = dialog && dialog.querySelector("[data-return-date]");
  const timeField = dialog && dialog.querySelector("[data-return-time]");
  let pending = null;

  if (dialog) {
    dialog.querySelector("[data-return-cancel]").addEventListener("click", () => dialog.close());
    // Clic sur le fond grisé : le formulaire remplit la fenêtre, un clic qui
    // atteint la <dialog> elle-même est donc hors du contenu.
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
    dialog.querySelector("form").addEventListener("submit", () => {
      const answer = pending;
      pending = null;
      if (answer) answer(dateField.value, timeField.value);
    });
  }

  // `date` : valeur proposée (celle de la mission). `onConfirm(date, heure)`
  // n'est appelé que si la fenêtre est validée.
  function ask(date, onConfirm) {
    if (!dialog) { onConfirm("", ""); return; }  // pas de fenêtre : on ne bloque pas
    if (date && !dateField.value) dateField.value = date;
    pending = onConfirm;
    dialog.showModal();
    timeField.focus();
  }

  // Fiche d'un OM : le bouton « Créer le retour » passe par la fenêtre.
  document.querySelectorAll("form[data-return-form]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (form.dataset.returnReady) return;  // deuxième passage : on laisse partir
      e.preventDefault();
      ask(form.dataset.returnDefaultDate, (date, time) => {
        form.querySelector('[name="return_date"]').value = date;
        form.querySelector('[name="return_time"]').value = time;
        form.dataset.returnReady = "1";
        form.requestSubmit();
      });
    });
  });

  return { ask };
})();

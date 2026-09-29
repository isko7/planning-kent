// Champs d'heure : la saisie libre d'un côté, un sélecteur heures / minutes
// de l'autre.
//
// Tous les champs d'heure de l'application portent `data-time`
// (missions/form.html, missions/_return_dialog.html, tours/planner.html).
// Ce sont des champs texte et non des input[type=time] : ces derniers
// s'affichent en AM/PM dès que le système n'est pas en français. La saisie
// ne change donc pas — « 6 », « 630 », « 6:30 » ou « 6h30 » donnent tous la
// même heure — et un clic dans le champ ouvre en plus deux colonnes, les
// heures et les minutes, dont chaque clic se voit aussitôt dans le champ.
//
// Tout est délégué au document : les lignes d'arrêts, de trajets et
// d'étapes sont clonées depuis des <template> au fil de la saisie, elles
// n'ont donc rien à initialiser.
//
// Le séparateur écrit dans le champ suit `data-time-format` : « h » pour
// 06h30 (Plan de Ramassage, fenêtre du retour), « : » par défaut (ordres de
// mission, qui stockent HH:MM — voir app/utils.py:normalize_time).
window.KentTime = (function () {
  const STEP = 5;         // pas de la colonne des minutes
  const DAY = 24 * 60;

  let panel = null;       // le sélecteur ouvert — il n'y en a jamais qu'un
  let owner = null;       // le champ qu'il sert

  // « 6 », « 630 », « 0630 », « 6:30 », « 6h30 » -> minutes depuis minuit.
  // null si ce n'est pas une heure : le champ garde alors sa valeur telle
  // quelle, bien visible.
  function parse(text) {
    const parts = (text || "").trim().split(/[\s:hH.,]+/).filter(Boolean);
    if (!parts.length) return null;
    let hours, minutes;
    if (parts.length >= 2) {
      hours = Number(parts[0]);
      minutes = Number(parts[1]);
    } else {
      const digits = parts[0];
      if (!/^\d{1,4}$/.test(digits)) return null;
      hours = Number(digits.length > 2 ? digits.slice(0, -2) : digits);
      minutes = digits.length > 2 ? Number(digits.slice(-2)) : 0;
    }
    if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
    if (hours > 23 || minutes > 59) return null;
    return hours * 60 + minutes;
  }

  // Minutes depuis minuit -> « 06h30 » ou « 06:30 », toujours sur 24 heures.
  function format(minutes, style) {
    const day = ((Math.round(minutes) % DAY) + DAY) % DAY;
    const hh = String(Math.floor(day / 60)).padStart(2, "0");
    const mm = String(day % 60).padStart(2, "0");
    return hh + (style === "h" ? "h" : ":") + mm;
  }

  function styleOf(input) {
    return input.dataset.timeFormat === "h" ? "h" : ":";
  }

  // Les écrans qui suivent ces champs écoutent `input` (récap des trajets du
  // formulaire OM, heure de référence du Plan de Ramassage) : une valeur
  // posée par le code doit les prévenir comme une frappe au clavier.
  function notify(input) {
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // --------------------------------------------------------- le sélecteur
  function build() {
    const box = document.createElement("div");
    box.className = "time-panel";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Choisir l'heure");
    box.appendChild(column("hour", "Heures", 24, 1));
    box.appendChild(column("minute", "Minutes", 60, STEP));
    // mousedown plutôt que click : se déclenche avant le blur du champ, qui
    // emporterait le sélecteur avant que le clic n'y arrive (même ruse que
    // address_autocomplete.js).
    box.addEventListener("mousedown", pick);
    return box;
  }

  function column(unit, title, count, step) {
    const col = document.createElement("div");
    col.className = "time-col";
    col.dataset.unit = unit;
    const head = document.createElement("div");
    head.className = "time-col__head";
    head.textContent = title;
    const list = document.createElement("div");
    list.className = "time-col__list";
    for (let value = 0; value < count; value += step) {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.tabIndex = -1;  // on y va à la souris ; au clavier, on tape l'heure
      cell.className = "time-cell";
      cell.dataset.value = String(value);
      cell.textContent = String(value).padStart(2, "0");
      list.appendChild(cell);
    }
    col.append(head, list);
    return col;
  }

  // Un clic ne pose qu'une moitié de l'heure : l'autre garde ce qu'elle
  // avait (00 si le champ était vide). Le sélecteur reste donc ouvert, le
  // temps de choisir les deux.
  function pick(e) {
    const cell = e.target.closest(".time-cell");
    if (!cell || !owner) return;
    e.preventDefault();
    const now = parse(owner.value) || 0;
    const value = Number(cell.dataset.value);
    const unit = cell.closest(".time-col").dataset.unit;
    apply(unit === "hour" ? value * 60 + (now % 60) : Math.floor(now / 60) * 60 + value);
  }

  function apply(minutes) {
    if (!owner) return;
    owner.value = format(minutes, styleOf(owner));
    notify(owner);
    mark(false);
  }

  function open(input) {
    if (owner === input) return;
    close();
    owner = input;
    panel = build();
    // Dans une fenêtre modale (« Créer le retour »), le sélecteur doit vivre
    // dans la <dialog> : tout ce qui est en dehors passe sous son voile.
    (input.closest("dialog") || document.body).appendChild(panel);
    place();
    mark(true);
  }

  function close() {
    if (panel) panel.remove();
    panel = null;
    owner = null;
  }

  // Sélecteur en position fixe sous le champ — et au-dessus s'il n'y a pas
  // la place en dessous (dernière ligne d'un tableau, bas de l'écran).
  function place() {
    if (!panel || !owner) return;
    const box = owner.getBoundingClientRect();
    const room = window.innerHeight - box.bottom - 8;
    const above = room < panel.offsetHeight && box.top > room;
    panel.style.left = Math.max(8, Math.min(box.left, window.innerWidth - panel.offsetWidth - 8)) + "px";
    panel.style.top = (above ? Math.max(8, box.top - panel.offsetHeight - 4) : box.bottom + 4) + "px";
  }

  // Les deux colonnes suivent ce qu'il y a dans le champ. Rien n'est allumé
  // dans les minutes quand la saisie tombe entre deux cases (13h07) : elle
  // est parfaitement valable, elle n'est simplement pas dans la colonne.
  // `center` amène la case choisie au milieu de sa colonne — à l'ouverture
  // seulement, pour ne pas déplacer la liste sous le curseur à chaque clic.
  function mark(center) {
    if (!panel || !owner) return;
    const minutes = parse(owner.value);
    const wanted = {
      hour: minutes == null ? null : Math.floor(minutes / 60),
      minute: minutes == null ? null : minutes % 60,
    };
    panel.querySelectorAll(".time-col").forEach((col) => {
      const target = wanted[col.dataset.unit];
      let chosen = null;
      col.querySelectorAll(".time-cell").forEach((cell) => {
        const on = target != null && Number(cell.dataset.value) === target;
        cell.classList.toggle("is-on", on);
        if (on) chosen = cell;
      });
      if (chosen) reveal(col.querySelector(".time-col__list"), chosen, center);
    });
  }

  // On ne touche qu'à l'ascenseur de la colonne : scrollIntoView ferait
  // aussi bouger la page sous le champ.
  function reveal(list, cell, center) {
    if (center) {
      list.scrollTop = cell.offsetTop - (list.clientHeight - cell.offsetHeight) / 2;
    } else if (cell.offsetTop < list.scrollTop) {
      list.scrollTop = cell.offsetTop;
    } else if (cell.offsetTop + cell.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = cell.offsetTop + cell.offsetHeight - list.clientHeight;
    }
  }

  // ↑ / ↓ : l'heure avance ou recule d'un pas de minutes, en se remettant
  // d'abord sur la grille de la colonne (13h07 puis ↓ donne 13h10).
  function shift(input, direction) {
    const now = parse(input.value);
    if (now == null) { apply(0); return; }
    const aligned = now % STEP === 0;
    apply(aligned ? now + direction * STEP
                  : (direction > 0 ? Math.ceil(now / STEP) : Math.floor(now / STEP)) * STEP);
  }

  // Sortie du champ : « 630 » devient « 06:30 ». Une saisie qui n'est pas une
  // heure reste telle quelle — le serveur la laisse passer de la même façon
  // (app/utils.py:normalize_time).
  function normalize(input) {
    const minutes = parse(input.value);
    if (minutes == null) return;
    const text = format(minutes, styleOf(input));
    if (text === input.value) return;
    input.value = text;
    notify(input);
  }

  // -------------------------------------------------------- les événements
  function fieldOf(target) {
    return target && target.closest ? target.closest("input[data-time]") : null;
  }

  document.addEventListener("pointerdown", (e) => {
    const input = fieldOf(e.target);
    if (input) open(input);
    else if (!e.target.closest || !e.target.closest(".time-panel")) close();
  });

  document.addEventListener("input", (e) => {
    const input = fieldOf(e.target);
    if (input && input === owner) mark(false);
  });

  document.addEventListener("keydown", (e) => {
    const input = fieldOf(e.target);
    if (!input) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      open(input);
      shift(input, e.key === "ArrowDown" ? 1 : -1);
    } else if (input !== owner) {
      return;
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();  // la fenêtre « Créer le retour » ne se ferme pas avec le sélecteur
      close();
    } else if (e.key === "Tab") {
      close();
    }
  });

  // blur ne remonte pas : écoute en phase de capture.
  document.addEventListener("blur", (e) => {
    const input = fieldOf(e.target);
    if (!input) return;
    normalize(input);
    if (input === owner) close();
  }, true);

  // La page bouge sous le sélecteur (ascenseur, rotation du téléphone) : il
  // suit son champ plutôt que de rester en l'air.
  window.addEventListener("scroll", place, true);
  window.addEventListener("resize", place);

  return { parse, format, close };
})();

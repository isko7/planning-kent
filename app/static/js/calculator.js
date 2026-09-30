// Calculatrice flottante, sur le modèle de celle des téléphones Samsung :
// on écrit l'opération en entier (« 1250+18% »), le résultat s'affiche sous
// la ligne au fur et à mesure, et « = » range l'opération dans l'historique.
//
// L'historique est là pour être réutilisé : un clic sur une ligne reprend son
// résultat dans l'opération en cours — c'est l'usage principal de cet écran.
// Il vit dans sessionStorage : il suit la navigation d'un onglet (chaque page
// est un chargement complet, l'application étant rendue par le serveur) et
// disparaît avec lui. Rien en base, rien sur le disque.
//
// Le clavier marche directement, pavé numérique compris : dès que le panneau
// est ouvert, les chiffres, les opérateurs, Entrée et Retour arrière lui
// reviennent — sauf si la frappe vise un champ de saisie de la page, qui
// reste prioritaire.
window.KentCalc = (function () {
  const HISTORY_KEY = "kent.calc.history.v1";
  const OPEN_KEY = "kent.calc.open";
  const MAX_HISTORY = 50;
  // Assez pour effacer le bruit des flottants (0.1+0.2) sans rogner un
  // résultat honnête : 12 chiffres significatifs.
  const PRECISION = 12;
  const THIN_SPACE = " ";  // espace fine insécable, séparateur des milliers

  const OPERATORS = "+-*/";
  const SHOWN = { "*": "×", "/": "÷", "-": "−", "+": "+" };

  let panel = null;      // le panneau flottant (KentFloat), créé au premier clic
  let expr = "";         // l'opération telle qu'elle est tapée, en ASCII
  let history = [];      // [{ expr, result }], le plus récent en tête
  let justEvaluated = false;  // « = » vient d'être pressé : un chiffre repart de zéro
  let nodes = {};

  // --------------------------------------------------------------- calcul
  // Analyse descendante plutôt qu'un eval() : ce qui est tapé ici ne doit
  // jamais être exécuté comme du JavaScript, et une opération incomplète
  // (« 12+ ») doit pouvoir échouer proprement, sans message.
  //
  // Le pourcentage suit la règle des calculatrices de téléphone, où il se lit
  // relativement à ce qui précède :
  //   200+10%  -> 220   (10 % de 200 ajoutés)
  //   200-10%  -> 180
  //   200*10%  -> 20    (10 % de 200)
  //   10%      -> 0,1
  function tokenize(text) {
    const tokens = [];
    let i = 0;
    while (i < text.length) {
      const c = text[i];
      if (c === " ") { i++; continue; }
      if (/[\d.]/.test(c)) {
        let n = "";
        while (i < text.length && /[\d.]/.test(text[i])) n += text[i++];
        tokens.push({ type: "number", value: n });
        continue;
      }
      if (OPERATORS.includes(c) || "()%".includes(c)) {
        tokens.push({ type: c });
        i++;
        continue;
      }
      throw new Error("caractère inattendu");
    }
    return tokens;
  }

  function parse(tokens) {
    let at = 0;
    const peek = () => (at < tokens.length ? tokens[at].type : null);

    // Un opérande porte sa valeur et le fait d'être un pourcentage : c'est
    // l'opération qui l'entoure qui décide de ce que « % » veut dire.
    function primary() {
      if (peek() === "-") { at++; const v = primary(); return { value: -v.value, percent: v.percent }; }
      if (peek() === "+") { at++; return primary(); }
      if (peek() === "(") {
        at++;
        const v = additive();
        if (peek() !== ")") throw new Error("parenthèse non fermée");
        at++;
        return postfix({ value: v, percent: false });
      }
      if (peek() === "number") {
        const raw = tokens[at++].value;
        const value = Number(raw);
        if (!isFinite(value)) throw new Error("nombre illisible");
        return postfix({ value, percent: false });
      }
      throw new Error("opération incomplète");
    }

    function postfix(operand) {
      while (peek() === "%") { at++; operand = { value: operand.value, percent: true }; }
      return operand;
    }

    function multiplicative() {
      let left = primary();
      while (peek() === "*" || peek() === "/") {
        const op = tokens[at++].type;
        const right = primary();
        // « × 10 % » : dix pour cent, donc 0,1 — pas dix pour cent de gauche.
        const value = right.percent ? right.value / 100 : right.value;
        if (op === "/" && value === 0) throw new Error("Division par zéro");
        left = { value: op === "*" ? left.value * value : left.value / value, percent: left.percent };
      }
      return left;
    }

    function additive() {
      let left = multiplicative();
      let value = left.percent ? left.value / 100 : left.value;
      while (peek() === "+" || peek() === "-") {
        const op = tokens[at++].type;
        const right = multiplicative();
        // « + 10 % » : dix pour cent de ce qui précède.
        const delta = right.percent ? (value * right.value) / 100 : right.value;
        value = op === "+" ? value + delta : value - delta;
      }
      return value;
    }

    const value = additive();
    if (at !== tokens.length) throw new Error("opération incomplète");
    return value;
  }

  // Valeur de l'opération en cours, ou null si elle n'en a pas encore une
  // (« 12+ », parenthèse ouverte…). L'erreur n'est rendue que lorsqu'elle est
  // parlante — une division par zéro, à dire à l'écran.
  function evaluate(text) {
    if (!text.trim()) return { value: null, error: null };
    try {
      const value = parse(tokenize(text));
      if (!isFinite(value)) return { value: null, error: "Résultat hors limites" };
      return { value: Number(value.toPrecision(PRECISION)), error: null };
    } catch (e) {
      return { value: null, error: e.message === "Division par zéro" ? e.message : null };
    }
  }

  // ------------------------------------------------------------ affichage
  // « 1234567.5 » -> « 1 234 567,5 » : séparateur de milliers fin, virgule
  // décimale — la notation française, comme partout ailleurs dans l'appli.
  function groupInteger(digits) {
    return digits.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_SPACE);
  }

  function formatNumber(value) {
    if (value === 0) return "0";
    const abs = Math.abs(value);
    if (abs >= 1e15 || abs < 1e-9) return String(value).replace(".", ",").replace("e", "e");
    const text = String(value);
    const [int, dec] = text.split(".");
    const sign = int.startsWith("-") ? "-" : "";
    return sign + groupInteger(int.replace("-", "")) + (dec ? "," + dec : "");
  }

  // Les nombres de l'opération sont mis en forme eux aussi, mais sans rien
  // arrondir : la partie décimale reste exactement ce qui a été tapé, sans
  // quoi « 1.0 » perdrait son zéro sous les doigts.
  function formatExpression(text) {
    return text.replace(/\d+(\.\d*)?/g, (n) => {
      const [int, dec] = n.split(".");
      return groupInteger(int) + (dec === undefined ? "" : "," + dec);
    }).replace(/[*/\-+]/g, (op) => SHOWN[op]);
  }

  function render() {
    const { value, error } = evaluate(expr);
    nodes.expr.textContent = formatExpression(expr);
    // La ligne du résultat suit l'opération : on reste collé à la fin.
    nodes.expr.scrollLeft = nodes.expr.scrollWidth;
    nodes.preview.classList.toggle("is-error", !!error);
    nodes.preview.textContent = error || (value == null || !expr ? "" : "= " + formatNumber(value));
    renderHistory();
  }

  function renderHistory() {
    const list = nodes.historyList;
    list.textContent = "";
    nodes.history.classList.toggle("is-empty", !history.length);
    history.forEach((entry) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "calc__history-item";
      button.title = "Reprendre ce résultat dans l'opération";
      const line = document.createElement("span");
      line.className = "calc__history-expr";
      line.textContent = formatExpression(entry.expr);
      const result = document.createElement("span");
      result.className = "calc__history-result";
      result.textContent = formatNumber(entry.result);
      button.append(line, result);
      button.addEventListener("click", () => reuse(entry.result));
      item.appendChild(button);
      list.appendChild(item);
    });
  }

  // ------------------------------------------------------------- mémoire
  function loadHistory() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]");
      return Array.isArray(saved)
        ? saved.filter((e) => e && typeof e.expr === "string" && typeof e.result === "number")
        : [];
    } catch (e) {
      return [];  // stockage bloqué : la calculatrice marche, sans mémoire
    }
  }

  function saveHistory() {
    try {
      sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, MAX_HISTORY)));
    } catch (e) { /* tant pis */ }
  }

  function remember(open) {
    try {
      sessionStorage.setItem(OPEN_KEY, open ? "1" : "0");
    } catch (e) { /* tant pis */ }
  }

  function wasOpen() {
    try {
      return sessionStorage.getItem(OPEN_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  // --------------------------------------------------------------- saisie
  const lastChar = () => expr.slice(-1);
  const endsWithOperator = () => OPERATORS.includes(lastChar());
  const openParens = () => (expr.match(/\(/g) || []).length - (expr.match(/\)/g) || []).length;

  // Le nombre en cours de frappe, à la fin de l'opération.
  function trailingNumber() {
    const m = expr.match(/(\d+(?:\.\d*)?)$/);
    return m ? { text: m[1], at: expr.length - m[1].length } : null;
  }

  function digit(d) {
    if (justEvaluated) { expr = ""; justEvaluated = false; }
    if (lastChar() === ")" || lastChar() === "%") expr += "*";
    expr += d;
  }

  function decimal() {
    if (justEvaluated) { expr = ""; justEvaluated = false; }
    const current = trailingNumber();
    if (current && current.text.includes(".")) return;   // un seul point par nombre
    if (!current) expr += "0";                            // « .5 » s'écrit « 0.5 »
    expr += ".";
  }

  function operator(op) {
    justEvaluated = false;
    if (!expr) {
      if (op === "-") expr = "-";                         // moins unaire
      return;
    }
    if (lastChar() === "(") {
      if (op === "-") expr += "-";
      return;
    }
    // Opérateur sur opérateur : c'est le dernier tapé qui vaut.
    if (endsWithOperator()) expr = expr.slice(0, -1);
    if (lastChar() === ".") expr = expr.slice(0, -1);
    expr += op;
  }

  // Une seule touche pour les deux parenthèses, comme sur le téléphone :
  // elle ferme si quelque chose est ouvert et qu'il y a de quoi fermer.
  function parenthesis() {
    justEvaluated = false;
    const canClose = openParens() > 0 && /[\d).%]/.test(lastChar());
    if (canClose) { expr += ")"; return; }
    if (/[\d).%]/.test(lastChar())) expr += "*";
    expr += "(";
  }

  function percent() {
    justEvaluated = false;
    if (/[\d)]/.test(lastChar())) expr += "%";
  }

  // « +/− » : change le signe du nombre en cours, en l'entourant au besoin
  // (« 5+3 » -> « 5+(-3) »), et le retire si le signe est déjà là.
  function toggleSign() {
    justEvaluated = false;
    const current = trailingNumber();
    if (!current) return;
    const before = expr.slice(0, current.at);
    if (before.endsWith("(-")) {
      expr = before.slice(0, -2) + current.text;
      if (expr.endsWith(")")) expr = expr.slice(0, -1);
      return;
    }
    if (before === "-") { expr = current.text; return; }
    expr = before + (before ? "(-" : "-") + current.text + (before ? ")" : "");
  }

  function backspace() {
    justEvaluated = false;
    expr = expr.slice(0, -1);
  }

  function clear() {
    justEvaluated = false;
    expr = "";
  }

  function equals() {
    const { value, error } = evaluate(expr);
    if (error || value == null || !expr.trim()) { render(); return; }
    // Une opération qui n'est qu'un nombre n'apprend rien à l'historique.
    if (String(value) !== expr.trim()) {
      history.unshift({ expr: expr.trim(), result: value });
      history = history.slice(0, MAX_HISTORY);
      saveHistory();
    }
    expr = String(value);
    justEvaluated = true;
  }

  // Résultat repris depuis l'historique : il remplace l'opération si elle
  // vient d'être calculée, et s'y ajoute sinon — comme un nombre tapé.
  function reuse(value) {
    if (justEvaluated) { expr = ""; justEvaluated = false; }
    if (/[\d).%]/.test(lastChar())) expr += "*";
    expr += String(value);
    render();
    nodes.expr.scrollLeft = nodes.expr.scrollWidth;
  }

  function clearHistory() {
    history = [];
    saveHistory();
    render();
  }

  // ------------------------------------------------------------- panneau
  const KEYS = [
    ["C", "clear", "calc__key--fn"], ["( )", "paren", "calc__key--fn"],
    ["%", "percent", "calc__key--fn"], ["÷", "/", "calc__key--op"],
    ["7", "7", ""], ["8", "8", ""], ["9", "9", ""], ["×", "*", "calc__key--op"],
    ["4", "4", ""], ["5", "5", ""], ["6", "6", ""], ["−", "-", "calc__key--op"],
    ["1", "1", ""], ["2", "2", ""], ["3", "3", ""], ["+", "+", "calc__key--op"],
    ["+/−", "sign", "calc__key--fn"], ["0", "0", ""], [",", ".", ""],
    ["=", "equals", "calc__key--equals"],
  ];

  function press(action) {
    if (/^\d$/.test(action)) digit(action);
    else if (OPERATORS.includes(action)) operator(action);
    else if (action === ".") decimal();
    else if (action === "paren") parenthesis();
    else if (action === "percent") percent();
    else if (action === "sign") toggleSign();
    else if (action === "clear") clear();
    else if (action === "back") backspace();
    else if (action === "equals") equals();
    render();
  }

  function build() {
    const float = window.KentFloat.create({
      name: "calc",
      label: "Calculatrice",
      className: "float-panel--calc",
      storageKey: "kent.calc.panel",
      // Haut par défaut : le clavier et l'affichage sont de taille fixe
      // (≈ 320 px), le reste est pour l'historique — qui n'a d'intérêt qu'en
      // montrant plusieurs lignes. Le panneau reste redimensionnable.
      width: 320,
      height: 560,
      onClose: () => { remember(false); toggle.classList.remove("is-active"); },
    });

    const body = float.body;
    body.innerHTML = `
      <div class="calc">
        <div class="calc__history" data-history>
          <div class="calc__history-head">
            <span>Historique</span>
            <button type="button" class="calc__history-clear" data-clear-history>Effacer</button>
          </div>
          <ol class="calc__history-list" data-history-list></ol>
          <div class="calc__history-empty">Les opérations validées se rangent ici.
            Un clic sur l'une d'elles en reprend le résultat.</div>
        </div>
        <div class="calc__display">
          <div class="calc__expr" data-expr aria-live="off"></div>
          <div class="calc__row">
            <div class="calc__preview" data-preview aria-live="polite"></div>
            <button type="button" class="calc__back" data-back title="Effacer le dernier caractère"
                    aria-label="Effacer le dernier caractère">⌫</button>
          </div>
        </div>
        <div class="calc__keys" data-keys></div>
      </div>`;

    nodes = {
      expr: body.querySelector("[data-expr]"),
      preview: body.querySelector("[data-preview]"),
      history: body.querySelector("[data-history]"),
      historyList: body.querySelector("[data-history-list]"),
    };

    const keys = body.querySelector("[data-keys]");
    KEYS.forEach(([label, action, className]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = ("calc__key " + className).trim();
      button.textContent = label;
      button.dataset.action = action;
      keys.appendChild(button);
    });
    keys.addEventListener("click", (e) => {
      const button = e.target.closest("[data-action]");
      if (button) press(button.dataset.action);
    });
    body.querySelector("[data-back]").addEventListener("click", () => press("back"));
    body.querySelector("[data-clear-history]").addEventListener("click", clearHistory);

    return float;
  }

  // ------------------------------------------------------------- clavier
  // Le panneau n'a aucun champ de saisie : les touches sont lues sur le
  // document. Une frappe qui vise un champ de la page lui revient — on ne
  // détourne pas la saisie d'un formulaire parce que la calculatrice est
  // ouverte.
  function isTypingElsewhere(target) {
    return !!(target && target.closest &&
      target.closest("input, textarea, select, [contenteditable=''], [contenteditable=true]"));
  }

  const KEY_ACTIONS = {
    "+": "+", "-": "-", "*": "*", "/": "/",
    ".": ".", ",": ".",
    "Enter": "equals", "=": "equals",
    "Backspace": "back", "Delete": "clear",
    "%": "percent", "(": "paren", ")": "paren",
  };

  // Le pavé numérique est lu par sa touche physique (`code`) et non par le
  // caractère produit : verrouillage numérique éteint, le 3 du pavé annonce
  // « PageDown ». Devant une calculatrice ouverte, c'est bien un 3 qui est
  // attendu.
  const NUMPAD_ACTIONS = {
    NumpadAdd: "+", NumpadSubtract: "-", NumpadMultiply: "*", NumpadDivide: "/",
    NumpadDecimal: ".", NumpadEnter: "equals", NumpadEqual: "equals",
  };

  function actionFor(e) {
    if (/^Numpad\d$/.test(e.code)) return e.code.slice(-1);
    if (NUMPAD_ACTIONS[e.code]) return NUMPAD_ACTIONS[e.code];
    if (/^\d$/.test(e.key)) return e.key;
    return KEY_ACTIONS[e.key];
  }

  function onKeyDown(e) {
    if (!panel || panel.panel.hidden) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    // Une frappe qui vise un champ de la page lui revient, Échap compris :
    // la calculatrice ne se ferme pas parce qu'on quitte un champ de saisie.
    if (isTypingElsewhere(e.target)) return;
    if (e.key === "Escape") { close(); return; }
    const action = actionFor(e);
    if (!action) return;
    e.preventDefault();
    press(action);
    flash(action);
  }

  // La touche du clavier s'allume comme si on l'avait cliquée : on voit ce
  // que la calculatrice a compris.
  function flash(action) {
    const button = panel.panel.querySelector(`[data-action="${CSS.escape(action)}"]`)
      || (action === "back" ? panel.panel.querySelector("[data-back]") : null);
    if (!button) return;
    button.classList.add("is-pressed");
    setTimeout(() => button.classList.remove("is-pressed"), 120);
  }

  // ---------------------------------------------------------- ouverture
  let toggle = null;

  function open() {
    if (!panel) {
      panel = build();
      history = loadHistory();
    }
    panel.open({ title: "Calculatrice" });
    toggle.classList.add("is-active");
    remember(true);
    render();
  }

  function close() {
    if (panel) panel.close();  // onClose retire l'état actif et la mémoire
  }

  function init() {
    toggle = document.getElementById("calc-open");
    if (!toggle || !window.KentFloat) return;
    toggle.addEventListener("click", () => {
      if (panel && !panel.panel.hidden) close();
      else open();
    });
    document.addEventListener("keydown", onKeyDown);
    // Rouverte telle qu'elle était : l'onglet garde la calculatrice ouverte
    // d'une page à l'autre, comme le bloc-notes.
    if (wasOpen()) open();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return { open, close };
})();

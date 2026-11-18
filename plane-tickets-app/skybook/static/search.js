// Progressive enhancement for the search form. The page works without this file.
(function () {
  "use strict";

  var origin = document.getElementById("origin");
  var destination = document.getElementById("destination");
  var swap = document.getElementById("swap-airports");
  if (!origin || !destination || !swap) {
    return;
  }

  // The button is hidden in the markup; show it only when this script runs.
  swap.hidden = false;
  swap.addEventListener("click", function () {
    var first = origin.value;
    origin.value = destination.value;
    destination.value = first;
  });
})();

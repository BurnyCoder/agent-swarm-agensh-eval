// Seat picker enhancement. The form works without this script: the server
// checks the seats on submit and explains any problem. This only makes the
// choice easier: it caps the boxes at the passenger count, shows progress and
// enables "Confirm seats" only when the count is exact.
(function () {
  "use strict";

  var form = document.getElementById("seat-form");
  if (!form) return;

  var passengers = parseInt(form.getAttribute("data-passengers"), 10) || 1;
  var counter = document.getElementById("seat-count");
  var submit = document.getElementById("seat-submit");
  var boxes = Array.prototype.slice.call(form.querySelectorAll('input[name="seat"]'));

  // Boxes the server rendered as taken. They stay disabled for good.
  var takenBoxes = boxes.filter(function (box) { return box.disabled; });

  function refresh() {
    var count = boxes.filter(function (box) { return box.checked; }).length;
    var full = count >= passengers;

    boxes.forEach(function (box) {
      var label = box.closest(".seat");
      if (label) label.classList.toggle("is-chosen", box.checked);
      if (takenBoxes.indexOf(box) !== -1) return;
      // Block new picks once the party is full; unticking always works.
      box.disabled = full && !box.checked;
    });

    if (counter) counter.textContent = count + " of " + passengers + " chosen";
    if (submit) submit.disabled = count !== passengers;
  }

  form.addEventListener("change", function (event) {
    if (event.target && event.target.name === "seat") refresh();
  });

  refresh();
})();

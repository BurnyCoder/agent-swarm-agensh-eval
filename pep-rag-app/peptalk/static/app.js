// PEPtalk web UI. Vanilla JS; all server text is inserted with textContent,
// never innerHTML, so PEP text cannot inject markup.
(function () {
  "use strict";

  var CITATION = /\[(PEP\s*\d+(?:\s*§[^\]]*)?)\]/g;

  var form = document.getElementById("ask-form");
  var questionEl = document.getElementById("question");
  var modeEl = document.getElementById("mode");
  var kEl = document.getElementById("k");
  var searchBtn = document.getElementById("search-btn");
  var statusEl = document.getElementById("status");
  var answerEl = document.getElementById("answer");
  var answerBody = document.getElementById("answer-body");
  var citedEl = document.getElementById("cited");
  var chunksEl = document.getElementById("chunks");
  var resultsTitle = document.getElementById("results-title");

  function setStatus(text, isError) {
    statusEl.textContent = text || "";
    statusEl.className = "status" + (isError ? " error" : "");
  }

  function params() {
    var k = parseInt(kEl.value, 10);
    if (!k || k < 1) k = 5;
    if (k > 20) k = 20;
    return { question: questionEl.value.trim(), mode: modeEl.value, k: k };
  }

  function errorText(response) {
    return response.json().then(function (body) {
      return body.detail || response.statusText;
    }, function () {
      return response.statusText;
    });
  }

  function norm(s) {
    return (s || "").replace(/§/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  // Map "[PEP 572 §Rationale]" to the first retrieved chunk with that PEP and
  // section, falling back to any chunk from the same PEP.
  function resolveCitation(inner, chunks) {
    var m = /PEP\s*(\d+)\s*(?:§\s*(.*))?$/.exec(inner.trim());
    if (!m) return null;
    var pep = parseInt(m[1], 10);
    var section = norm(m[2] || "");
    var exact = null, samePep = null;
    for (var i = 0; i < chunks.length; i++) {
      var c = chunks[i];
      if (Number(c.pep) !== pep) continue;
      if (!samePep) samePep = c;
      if (section && norm(c.section) === section) { exact = c; break; }
    }
    return exact || samePep;
  }

  function renderAnswer(text, chunks) {
    answerBody.textContent = "";
    var last = 0, match;
    CITATION.lastIndex = 0;
    while ((match = CITATION.exec(text)) !== null) {
      if (match.index > last) {
        answerBody.appendChild(document.createTextNode(text.slice(last, match.index)));
      }
      var target = resolveCitation(match[1], chunks);
      var label = "[" + match[1] + "]";
      if (target) {
        var a = document.createElement("a");
        a.className = "citation";
        a.href = "#" + target.anchor;
        a.textContent = label;
        a.title = "Jump to the retrieved snippet";
        answerBody.appendChild(a);
      } else {
        // Cited but not in the retrieved context: show it, flagged, not linked.
        var span = document.createElement("span");
        span.className = "citation unresolved";
        span.title = "Not in the retrieved context";
        span.textContent = label;
        answerBody.appendChild(span);
      }
      last = CITATION.lastIndex;
    }
    if (last < text.length) {
      answerBody.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  function renderCited(cited, chunks) {
    citedEl.textContent = "";
    if (!cited || cited.length === 0) {
      var li = document.createElement("li");
      li.textContent = "No sources cited.";
      citedEl.appendChild(li);
      return;
    }
    cited.forEach(function (c) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#" + c.anchor;
      a.textContent = "PEP " + c.pep + (c.section ? " §" + c.section : "") + " — " + c.title;
      li.appendChild(a);
      citedEl.appendChild(li);
    });
  }

  function renderChunks(chunks) {
    chunksEl.textContent = "";
    chunks.forEach(function (c) {
      var li = document.createElement("li");
      li.className = "chunk";
      li.id = c.anchor;

      var head = document.createElement("div");
      head.className = "chunk-head";
      var title = document.createElement("strong");
      title.textContent = "PEP " + c.pep + " — " + c.title;
      head.appendChild(title);
      if (c.section) {
        var sec = document.createElement("span");
        sec.className = "section";
        sec.textContent = " §" + c.section;
        head.appendChild(sec);
      }
      var score = document.createElement("span");
      score.className = "score";
      score.textContent = "score " + Number(c.score).toFixed(3);
      head.appendChild(score);
      li.appendChild(head);

      var snip = document.createElement("p");
      snip.className = "snippet";
      snip.textContent = c.snippet;
      li.appendChild(snip);
      chunksEl.appendChild(li);
    });
  }

  // Give every chunk a stable DOM anchor and a display key.
  function prepare(chunks) {
    return (chunks || []).map(function (c, i) {
      c.anchor = "chunk-" + (c.chunk_id != null ? c.chunk_id : i + 1);
      return c;
    });
  }

  function doSearch(p) {
    var qs = new URLSearchParams({ q: p.question, mode: p.mode, k: String(p.k) });
    return fetch("/api/search?" + qs.toString(), { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) return errorText(r).then(function (t) { throw new Error(t); });
        return r.json();
      })
      .then(function (body) {
        var chunks = prepare(body.results);
        renderChunks(chunks);
        resultsTitle.textContent = "Retrieved snippets (" + chunks.length + ")";
        return chunks;
      });
  }

  function doAsk(p) {
    return fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ question: p.question, mode: p.mode, k: p.k })
    }).then(function (r) {
      if (!r.ok) return errorText(r).then(function (t) { throw new Error(t); });
      return r.json();
    }).then(function (body) {
      var chunks = prepare(body.chunks);
      renderAnswer(body.answer || "", chunks);
      // "cited" holds the retrieved chunks the answer used; link each to its snippet.
      // "cited" entries are chunk ids (or hit objects carrying one); keep only
      // the ones that resolve to a retrieved chunk.
      var cited = (body.cited || []).map(function (c) {
        var id = (c !== null && typeof c === "object") ? c.chunk_id : c;
        return chunks.filter(function (x) { return String(x.chunk_id) === String(id); })[0];
      }).filter(Boolean);
      renderCited(cited, chunks);
      renderChunks(chunks);
      resultsTitle.textContent = "Retrieved snippets (" + chunks.length + ")";
      answerEl.hidden = false;
    });
  }

  function busy(on) {
    form.setAttribute("aria-busy", on ? "true" : "false");
    searchBtn.disabled = on;
    document.getElementById("ask-btn").disabled = on;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var p = params();
    if (!p.question) return;
    busy(true);
    setStatus("Asking Claude…");
    doAsk(p)
      .then(function () { setStatus(""); })
      .catch(function (err) { setStatus("Ask failed: " + err.message, true); })
      .then(function () { busy(false); });
  });

  searchBtn.addEventListener("click", function () {
    var p = params();
    if (!p.question) return;
    busy(true);
    setStatus("Searching…");
    answerEl.hidden = true;
    doSearch(p)
      .then(function () { setStatus(""); })
      .catch(function (err) { setStatus("Search failed: " + err.message, true); })
      .then(function () { busy(false); });
  });
})();

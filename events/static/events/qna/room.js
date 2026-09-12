(function () {
  "use strict";
  var qna = window.EventQna;
  var list = document.getElementById("qna-list");
  var empty = document.getElementById("qna-empty");
  var counts = document.getElementById("qna-counts");
  var sort = document.getElementById("qna-sort");
  var form = document.getElementById("qna-question-form");
  var pending = [];
  var lastItems = [];
  var lastTotals = { visible: 0, answered: 0 };
  // Vote failures stay attached to their question until a later attempt
  // succeeds; a silent revert left keyboard users with no feedback (UX-06).
  var voteNotes = {};

  function voteMessage(failure) {
    return (failure && failure.message ? failure.message : "The vote failed") +
      " — your vote was not saved.";
  }

  function onVote(item, update) {
    var oldScore = item.score;
    var oldVoted = item.voted;
    item.voted = !oldVoted;
    item.score = Math.max(0, oldScore + (item.voted ? 1 : -1));
    update(item);
    qna.api("questions/" + encodeURIComponent(item.question_id) + "/vote/", {
      method: item.voted ? "POST" : "DELETE",
    }).then(function (result) {
      item.score = result.value.score;
      item.voted = result.value.voted;
      delete voteNotes[item.question_id];
      drawFromCache();
      refresh();
    }).catch(function (failure) {
      item.score = oldScore;
      item.voted = oldVoted;
      voteNotes[item.question_id] = voteMessage(failure);
      drawFromCache();
    });
  }

  function buildRow(li, item) {
    var key = String(item.question_id);
    var text = document.createElement("p");
    text.className = "qna-question-text";
    li.appendChild(text);
    var meta = document.createElement("p");
    meta.className = "qna-meta";
    li.appendChild(meta);
    var note = document.createElement("p");
    note.className = "qna-item-note";
    note.setAttribute("role", "status");
    li.appendChild(note);
    var vote = null;
    var current = item;
    function update(item) {
      current = item;
      li.classList.toggle("is-pinned", !!item.pinned);
      li.classList.toggle("is-pending", !!item.pending);
      text.textContent = item.text;
      meta.textContent = item.pending
        ? (item.author_name || "Anonymous") + " · Submitting…"
        : (item.author_name || "Anonymous") + " · " + item.score + " votes";
      if (item.question_id && !item.pending) {
        if (!vote) {
          vote = document.createElement("button");
          vote.type = "button";
          vote.className = "qna-vote";
          vote.setAttribute("data-qna-action", "vote");
          vote.addEventListener("click", function () { onVote(current, update); });
          li.insertBefore(vote, note);
        }
        vote.textContent = (item.voted ? "Remove vote" : "Upvote") + " (" + item.score + ")";
        vote.setAttribute("aria-pressed", item.voted ? "true" : "false");
      }
      note.textContent = item.pending ? "" : voteNotes[key] || "";
    }
    update(item);
    return update;
  }

  var renderList = qna.createList(list, {
    key: function (item) { return item.question_id; },
    build: buildRow,
    removalMessage: function (item) {
      return item.pending ? "" : "A question was removed from the list.";
    },
  });

  function pruneNotes(items) {
    var live = {};
    items.forEach(function (item) { live[item.question_id] = true; });
    Object.keys(voteNotes).forEach(function (key) {
      if (!live[key]) delete voteNotes[key];
    });
  }

  function drawAll(items, totals) {
    if (!list) return;
    var everything = items.concat(pending);
    pruneNotes(everything);
    renderList(everything);
    if (empty) empty.hidden = everything.length !== 0;
    if (counts) counts.textContent = (totals.visible || 0) + " visible · " + (totals.answered || 0) + " answered";
  }

  function render(items, totals) {
    lastItems = items;
    lastTotals = totals;
    drawAll(items, totals);
  }

  // A pending submission overlays the last successful server state. Redrawing
  // from an empty list would hide real questions while a request is in flight
  // or after it fails, because an unchanged 304 poll never rerenders.
  function drawFromCache() {
    drawAll(lastItems, lastTotals);
  }

  function refresh() {
    qna.poll(sort ? sort.value : "popular", render);
  }
  if (sort) sort.addEventListener("change", refresh);
  if (form) form.addEventListener("submit", function (event) {
    event.preventDefault();
    var submit = form.querySelector('button[type="submit"]');
    if (submit && submit.disabled) return;
    var text = form.elements.text.value.trim();
    var name = form.elements.author_name ? form.elements.author_name.value.trim() : "";
    var error = document.getElementById("qna-form-error");
    if (error) error.textContent = "";
    var optimistic = { question_id: "pending-" + Date.now(), text: text, author_name: name, score: 1, pending: true };
    pending.push(optimistic);
    drawFromCache();
    if (submit) submit.disabled = true;
    function release() {
      if (submit) submit.disabled = false;
    }
    qna.api("questions/", { method: "POST", body: { text: text, author_name: name } })
      .then(function () {
        pending = pending.filter(function (item) { return item !== optimistic; });
        form.reset();
        release();
        refresh();
      })
      .catch(function (failure) {
        pending = pending.filter(function (item) { return item !== optimistic; });
        if (error) error.textContent = failure.message;
        drawFromCache();
        release();
      });
  });
  qna.startPolling(function () { return sort ? sort.value : "popular"; }, render, 4000);
}());

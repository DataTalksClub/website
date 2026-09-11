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

  function render(items, totals) {
    if (!list) return;
    lastItems = items;
    lastTotals = totals;
    draw(items, totals);
  }

  // A pending submission overlays the last successful server state. Redrawing
  // from an empty list would hide real questions while a request is in flight
  // or after it fails, because an unchanged 304 poll never rerenders.
  function drawFromCache() {
    if (!list) return;
    draw(lastItems, lastTotals);
  }

  function draw(items, totals) {
    list.textContent = "";
    items.concat(pending).forEach(function (item) {
      var li = document.createElement("li");
      li.className =
        "qna-item" +
        (item.pinned ? " is-pinned" : "") +
        (item.pending ? " is-pending" : "");
      var text = document.createElement("p");
      text.className = "qna-question-text";
      text.textContent = item.text;
      li.appendChild(text);
      var meta = document.createElement("p");
      meta.className = "qna-meta";
      meta.textContent = item.pending
        ? (item.author_name || "Anonymous") + " · Submitting…"
        : (item.author_name || "Anonymous") + " · " + item.score + " votes";
      li.appendChild(meta);
      if (item.question_id && !item.pending) {
        var vote = document.createElement("button");
        vote.type = "button";
        vote.className = "qna-vote";
        vote.textContent = (item.voted ? "Remove vote" : "Upvote") + " (" + item.score + ")";
        vote.setAttribute("aria-pressed", item.voted ? "true" : "false");
        vote.addEventListener("click", function () {
          var oldScore = item.score;
          var oldVoted = item.voted;
          item.voted = !oldVoted;
          item.score = Math.max(0, oldScore + (item.voted ? 1 : -1));
          drawFromCache();
          qna.api("questions/" + encodeURIComponent(item.question_id) + "/vote/", {
            method: item.voted ? "POST" : "DELETE",
          }).then(function (result) {
            item.score = result.value.score;
            item.voted = result.value.voted;
            qna.poll(sort ? sort.value : "popular", render);
          }).catch(function () {
            item.score = oldScore;
            item.voted = oldVoted;
            drawFromCache();
          });
        });
        li.appendChild(vote);
      }
      list.appendChild(li);
    });
    if (empty) empty.hidden = items.length + pending.length !== 0;
    if (counts) counts.textContent = (totals.visible || 0) + " visible · " + (totals.answered || 0) + " answered";
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

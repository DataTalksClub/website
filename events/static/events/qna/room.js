(function () {
  "use strict";
  var qna = window.EventQna;
  var list = document.getElementById("qna-list");
  var empty = document.getElementById("qna-empty");
  var counts = document.getElementById("qna-counts");
  var sort = document.getElementById("qna-sort");
  var form = document.getElementById("qna-question-form");
  var pending = [];

  function render(items, totals) {
    if (!list) return;
    list.textContent = "";
    items.concat(pending).forEach(function (item) {
      var li = document.createElement("li");
      li.className = "qna-item" + (item.pinned ? " is-pinned" : "");
      var text = document.createElement("p");
      text.className = "qna-question-text";
      text.textContent = item.text;
      li.appendChild(text);
      var meta = document.createElement("p");
      meta.className = "qna-meta";
      meta.textContent = (item.author_name || "Anonymous") + " · " + item.score + " votes";
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
          render(items, totals);
          qna.api("questions/" + encodeURIComponent(item.question_id) + "/vote/", {
            method: item.voted ? "POST" : "DELETE",
          }).then(function (result) {
            item.score = result.value.score;
            item.voted = result.value.voted;
            qna.poll(sort ? sort.value : "popular", render);
          }).catch(function () {
            item.score = oldScore;
            item.voted = oldVoted;
            render(items, totals);
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
    var text = form.elements.text.value.trim();
    var name = form.elements.author_name ? form.elements.author_name.value.trim() : "";
    var error = document.getElementById("qna-form-error");
    var optimistic = { question_id: "pending-" + Date.now(), text: text, author_name: name, score: 1, pending: true };
    pending.push(optimistic);
    render([], { visible: 0, answered: 0 });
    qna.api("questions/", { method: "POST", body: { text: text, author_name: name } })
      .then(function () { pending = []; form.reset(); refresh(); })
      .catch(function (failure) {
        pending = pending.filter(function (item) { return item !== optimistic; });
        if (error) error.textContent = failure.message;
        refresh();
      });
  });
  qna.startPolling(function () { return sort ? sort.value : "popular"; }, render, 4000);
}());

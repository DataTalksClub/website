(function () {
  "use strict";
  var qna = window.EventQna;
  var list = document.getElementById("qna-list");
  var counts = document.getElementById("qna-counts");

  function render(items, totals) {
    if (!list) return;
    list.textContent = "";
    items.forEach(function (item) {
      var li = document.createElement("li");
      li.className = "qna-item" + (item.pinned ? " is-pinned" : "");
      var text = document.createElement("p");
      text.className = "qna-question-text";
      text.textContent = item.text;
      li.appendChild(text);
      var meta = document.createElement("p");
      meta.className = "qna-meta";
      meta.textContent = (item.author_name || "Anonymous") + " · " + item.score + " votes · " + item.status;
      li.appendChild(meta);
      [
        [item.status === "answered" ? "Unanswer" : "Answer", { status: item.status === "answered" ? "visible" : "answered" }],
        [item.pinned ? "Unpin" : "Pin", { pinned: !item.pinned }],
        ["Delete", { status: "deleted" }],
      ].forEach(function (action) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "qna-button qna-button-small";
        button.textContent = action[0];
        button.addEventListener("click", function () {
          var old = { status: item.status, pinned: item.pinned };
          Object.keys(action[1]).forEach(function (key) { item[key] = action[1][key]; });
          render(items, totals);
          qna.api("questions/" + encodeURIComponent(item.question_id) + "/", {
            method: "PATCH", body: action[1],
          }).then(function () { qna.poll("popular", render); }).catch(function () {
            item.status = old.status; item.pinned = old.pinned; render(items, totals);
          });
        });
        li.appendChild(button);
      });
      list.appendChild(li);
    });
    if (counts) counts.textContent = (totals.visible || 0) + " visible · " + (totals.answered || 0) + " answered";
  }
  qna.startPolling("popular", render, 5000);
}());

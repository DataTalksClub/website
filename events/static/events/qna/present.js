(function () {
  "use strict";
  var qna = window.EventQna;
  var list = document.getElementById("qna-list");
  function render(items) {
    if (!list) return;
    list.textContent = "";
    items.filter(function (item) { return item.status !== "deleted"; }).forEach(function (item) {
      var li = document.createElement("li");
      li.className = "qna-item qna-present-item" + (item.pinned ? " is-pinned" : "");
      var text = document.createElement("p");
      text.className = "qna-question-text";
      text.textContent = item.text;
      li.appendChild(text);
      var meta = document.createElement("p");
      meta.className = "qna-meta";
      meta.textContent = (item.author_name || "Anonymous") + " · " + item.score + " votes";
      li.appendChild(meta);
      list.appendChild(li);
    });
    var empty = document.getElementById("qna-empty");
    if (empty) empty.hidden = items.length !== 0;
  }
  qna.startPolling("popular", render, 1000);
}());

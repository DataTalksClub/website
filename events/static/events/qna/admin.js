(function () {
  "use strict";
  var qna = window.EventQna;
  var list = document.getElementById("qna-list");
  var counts = document.getElementById("qna-counts");
  // Failed moderation actions report next to their control until a later
  // attempt succeeds; a silent revert left keyboard users with no
  // feedback (UX-06).
  var actionNotes = {};

  var ACTIONS = [
    {
      name: "status",
      label: function (item) { return item.status === "answered" ? "Unanswer" : "Answer"; },
      payload: function (item) { return { status: item.status === "answered" ? "visible" : "answered" }; },
    },
    {
      name: "pin",
      label: function (item) { return item.pinned ? "Unpin" : "Pin"; },
      payload: function (item) { return { pinned: !item.pinned }; },
    },
    {
      name: "delete",
      label: function () { return "Delete"; },
      payload: function () { return { status: "deleted" }; },
    },
  ];

  function onAction(item, action) {
    var payload = action.payload(item);
    var old = { status: item.status, pinned: item.pinned };
    Object.keys(payload).forEach(function (key) { item[key] = payload[key]; });
    rerender();
    qna.api("questions/" + encodeURIComponent(item.question_id) + "/", {
      method: "PATCH", body: payload,
    }).then(function () {
      delete actionNotes[item.question_id];
      refresh();
    }).catch(function (failure) {
      item.status = old.status;
      item.pinned = old.pinned;
      actionNotes[item.question_id] =
        (failure && failure.message ? failure.message : "The change failed") +
        " — the moderation change was not saved.";
      rerender();
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
    var buttons = {};
    var current = item;
    function update(item) {
      current = item;
      li.classList.toggle("is-pinned", !!item.pinned);
      text.textContent = item.text;
      meta.textContent = (item.author_name || "Anonymous") + " · " + item.score + " votes · " + item.status;
      ACTIONS.forEach(function (action) {
        if (!buttons[action.name]) {
          var button = document.createElement("button");
          button.type = "button";
          button.className = "qna-button qna-button-small";
          button.setAttribute("data-qna-action", action.name);
          button.addEventListener("click", function () {
            onAction(current, action);
          });
          li.insertBefore(button, note);
          buttons[action.name] = button;
        }
        buttons[action.name].textContent = action.label(item);
      });
      note.textContent = actionNotes[key] || "";
    }
    update(item);
    return update;
  }

  var renderList = qna.createList(list, {
    key: function (item) { return item.question_id; },
    build: buildRow,
    removalMessage: function () { return "A question was removed from the list."; },
  });

  var lastItems = [];
  var lastTotals = { visible: 0, answered: 0 };

  function rerender() {
    if (!list) return;
    // Deleted is terminal and excluded from every collection: the row
    // leaves the list immediately instead of lingering until the next
    // poll confirms it.
    renderList(lastItems.filter(function (item) { return item.status !== "deleted"; }));
    if (counts) {
      counts.textContent = (lastTotals.visible || 0) + " visible · " + (lastTotals.answered || 0) + " answered";
    }
  }

  function render(value) {
    lastItems = value.items || [];
    lastTotals = value.counts || {};
    // UX-07: the host's banner follows the session state the poll just
    // vouched for, so a lifecycle change in Studio shows up here too.
    var banner = document.getElementById("qna-banner");
    if (banner && value.state) {
      banner.textContent = value.state.charAt(0).toUpperCase() + value.state.slice(1);
    }
    rerender();
  }

  function refresh() {
    qna.poll("popular", render);
  }
  qna.startPolling("popular", render, 5000);
}());

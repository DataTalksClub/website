/*
 * Shared submit-confirmation listener for Studio Courses forms.
 *
 * Forms that need a confirmation declare their message as data:
 *
 *   <form method="post" data-confirm-message="Score all submissions for X?">
 *
 * The message is plain data — Django's normal attribute escaping makes it
 * safe — and is never concatenated into executable JavaScript. Registering
 * one document-level listener also keeps duplicate copies of the old inline
 * handler from appearing per form.
 */
(function () {
  "use strict";

  if (window.__studioConfirmSubmitInstalled) {
    return;
  }
  window.__studioConfirmSubmitInstalled = true;

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form && form.dataset ? form.dataset.confirmMessage : null;
    if (!message) {
      return;
    }
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });
})();

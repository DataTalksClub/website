/* A picture that fails to load never leaves a broken box behind.

   The server already draws no picture box when a record has no picture, and a
   person with no portrait gets their initials (public/_person_chip.html).  This
   covers the other case: a picture the record names that does not arrive (a
   restart mid-load, a missing media file).  A person's portrait becomes the
   same initials disc the server draws; a card's thumbnail is removed, so the
   card reads as one without a picture.

   Images that already failed before this script ran are caught by the scan
   below; lazy ones that fail later by the capture-phase listener (error events
   do not bubble). */
(function () {
  'use strict';

  function replacePortrait(img) {
    var disc = document.createElement('span');
    disc.className = 'person-chip-portrait person-chip-initials';
    disc.setAttribute('aria-hidden', 'true');
    disc.textContent = img.getAttribute('data-initials') || '';
    img.replaceWith(disc);
  }

  function handle(img) {
    if (!img || img.tagName !== 'IMG' || !img.parentNode) {
      return;
    }
    if (img.classList.contains('person-chip-portrait')) {
      replacePortrait(img);
    } else if (img.classList.contains('event-item-thumb')) {
      img.remove();
    }
  }

  document.addEventListener(
    'error',
    function (event) {
      handle(event.target);
    },
    true
  );

  var images = document.querySelectorAll('img.person-chip-portrait, img.event-item-thumb');
  Array.prototype.forEach.call(images, function (img) {
    if (img.complete && img.naturalWidth === 0 && img.getAttribute('src')) {
      handle(img);
    }
  });
})();

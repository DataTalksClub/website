document.addEventListener('DOMContentLoaded', function() {
  var addButton = document.getElementById('add-learning-public-link');
  var linksContainer = document.getElementById('learning-in-public-links');
  var fieldContainer = document.getElementById('learning-in-public-links-container');
  var rowTemplate = document.getElementById('learning-in-public-row-template');
  var capNote = document.getElementById('learning-in-public-cap-note');

  if (!addButton || !linksContainer || !rowTemplate) {
    return;
  }

  function markCapReached() {
    addButton.disabled = true;
    if (capNote) {
      capNote.hidden = false;
    }
  }

  addButton.addEventListener('click', function() {
    var currentLinkCount = linksContainer.querySelectorAll('input[type="url"]').length;
    var cap = Number(
      fieldContainer ? fieldContainer.dataset.learningInPublicCap : 0
    );

    if (currentLinkCount >= cap) {
      markCapReached();
      return;
    }

    var nextIndex = currentLinkCount + 1;
    var holder = document.createElement('div');
    holder.innerHTML = rowTemplate.innerHTML.replaceAll('INDEX', String(nextIndex));
    while (holder.firstChild) {
      linksContainer.appendChild(holder.firstChild);
    }

    var addedInput = linksContainer.querySelector(
      '#learning-in-public-link-' + nextIndex
    );
    if (addedInput) {
      addedInput.focus();
    }

    if (nextIndex >= cap) {
      markCapReached();
    }
  });
});

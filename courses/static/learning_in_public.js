document.addEventListener('DOMContentLoaded', function() {
  var addButton = document.getElementById('add-learning-public-link');
  var linksContainer = document.getElementById('learning-in-public-links');
  var fieldContainer = document.getElementById('learning-in-public-links-container');
  var rowTemplate = document.getElementById('learning-in-public-row-template');
  var capNote = document.getElementById('learning-in-public-cap-note');

  if (!linksContainer) {
    return;
  }

  function rowCount() {
    return linksContainer.querySelectorAll('.link-row').length;
  }

  function capValue() {
    return Number(
      fieldContainer ? fieldContainer.dataset.learningInPublicCap : 0
    );
  }

  function markCapReached() {
    if (!addButton) {
      return;
    }
    addButton.disabled = true;
    if (capNote) {
      capNote.hidden = false;
    }
  }

  function markCapNotReached() {
    if (!addButton) {
      return;
    }
    addButton.disabled = false;
    if (capNote) {
      capNote.hidden = true;
    }
  }

  function renumberRows() {
    var rows = linksContainer.querySelectorAll('.link-row');
    rows.forEach(function(row, position) {
      var index = position + 1;
      var label = row.querySelector('label');
      var input = row.querySelector('input[type="url"]');
      var feedback = row.querySelector('.invalid-feedback');
      var removeButton = row.querySelector('.link-remove');

      if (label) {
        label.setAttribute('for', 'learning-in-public-link-' + index);
        label.textContent = 'Learning in public link ' + index;
      }
      if (input) {
        input.id = 'learning-in-public-link-' + index;
      }
      if (feedback) {
        feedback.id = 'learning-in-public-link-' + index + '-error';
      }
      if (removeButton) {
        removeButton.setAttribute('aria-label', 'Remove link ' + index);
      }
    });
  }

  function clearRow(row) {
    var input = row.querySelector('input[type="url"]');
    if (!input) {
      return;
    }
    input.value = '';
    input.classList.remove('is-invalid');
    input.removeAttribute('aria-invalid');
    input.removeAttribute('aria-errormessage');
    input.setCustomValidity('');
    var feedback = row.querySelector('.invalid-feedback');
    if (feedback) {
      feedback.textContent = '';
    }
    input.focus();
  }

  function bindRemove(row) {
    var removeButton = row.querySelector('.link-remove');
    if (!removeButton) {
      return;
    }
    removeButton.addEventListener('click', function() {
      if (rowCount() <= 1) {
        // Always keep at least one row on the page: clear it in place
        // instead of leaving a field with no way to add a link back.
        clearRow(row);
      } else {
        row.remove();
        renumberRows();
        var rows = linksContainer.querySelectorAll('.link-row');
        var previousInput = rows.length
          ? rows[rows.length - 1].querySelector('input[type="url"]')
          : null;
        if (previousInput) {
          previousInput.focus();
        }
      }
      if (rowCount() < capValue()) {
        markCapNotReached();
      }
    });
  }

  linksContainer.querySelectorAll('.link-row').forEach(bindRemove);

  if (!addButton || !rowTemplate) {
    return;
  }

  addButton.addEventListener('click', function() {
    var currentLinkCount = rowCount();
    var cap = capValue();

    if (currentLinkCount >= cap) {
      markCapReached();
      return;
    }

    var nextIndex = currentLinkCount + 1;
    var holder = document.createElement('div');
    holder.innerHTML = rowTemplate.innerHTML.replaceAll('INDEX', String(nextIndex));
    var addedRow = holder.firstElementChild;
    while (holder.firstChild) {
      linksContainer.appendChild(holder.firstChild);
    }

    if (addedRow) {
      bindRemove(addedRow);
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

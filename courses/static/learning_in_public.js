document.addEventListener('DOMContentLoaded', function() {
  var addButton = document.getElementById('add-learning-public-link');
  var linksContainer = document.getElementById('learning-in-public-links');
  var fieldContainer = document.getElementById('learning-in-public-links-container');

  if (!addButton || !linksContainer) {
    return;
  }

  addButton.addEventListener('click', function() {
    var currentLinkCount = linksContainer.querySelectorAll('input[type="url"]').length;
    var cap = Number(
      fieldContainer ? fieldContainer.dataset.learningInPublicCap : 0
    );

    if (currentLinkCount < cap) {
      var input = document.createElement('input');
      input.type = 'url';
      input.className = 'field-input form-control';
      input.name = 'learning_in_public_links[]';
      input.autocomplete = 'url';
      input.inputMode = 'url';
      linksContainer.appendChild(input);
    }

    if (currentLinkCount + 1 >= cap) {
      addButton.disabled = true;
    }
  });
});

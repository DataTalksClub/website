document.addEventListener('DOMContentLoaded', function() {
  var form = document.getElementById('homework-form');
  if (!form) {
    return;
  }

  var questions = form.querySelectorAll('fieldset.question');
  if (!questions.length) {
    return;
  }

  function questionId(fieldset) {
    var li = fieldset.closest('li[id]');
    return li ? li.id : null;
  }

  function isAnswered(fieldset) {
    var controls = fieldset.querySelectorAll('input, textarea');
    for (var i = 0; i < controls.length; i++) {
      var control = controls[i];
      if (control.type === 'radio' || control.type === 'checkbox') {
        if (control.checked) {
          return true;
        }
      } else if (control.value.trim() !== '') {
        return true;
      }
    }
    return false;
  }

  function refresh() {
    var answered = 0;
    questions.forEach(function(fieldset) {
      var done = isAnswered(fieldset);
      if (done) {
        answered += 1;
      }
      var disc = fieldset.querySelector('.question-number');
      if (disc) {
        disc.setAttribute('data-answered', done ? 'true' : 'false');
      }
      var id = questionId(fieldset);
      if (id) {
        var mark = document.querySelector(
          '.question-map a[href="#' + id + '"]'
        );
        if (mark) {
          mark.setAttribute('data-answered', done ? 'true' : 'false');
          var label = mark.textContent.trim();
          mark.setAttribute(
            'aria-label',
            'Question ' + label + (done ? ', answered' : ', not answered')
          );
        }
      }
    });

    document.querySelectorAll('[data-answered-count]').forEach(function(el) {
      el.textContent = String(answered);
    });

    var bar = document.querySelector('.rail .progress');
    if (bar) {
      bar.setAttribute('aria-valuenow', String(answered));
      var fill = bar.querySelector('.progress-fill');
      if (fill) {
        fill.style.width = (answered / questions.length * 100) + '%';
      }
    }
  }

  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  refresh();
});

document.addEventListener('DOMContentLoaded', function() {
  var csrfToken = document.getElementById('csrf-token')?.value || '';

  function emailPreferenceInputs() {
    return Array.prototype.slice.call(
      document.querySelectorAll('.js-email-preference-toggle')
    );
  }

  function setEmailPreferencesDisabled(disabled) {
    emailPreferenceInputs().forEach(function(input) {
      input.disabled = disabled;
    });
  }

  function setEmailPreferencesStatus(message, busy) {
    var status = document.querySelector('.js-email-preferences-status');
    if (status) {
      status.textContent = message;
      status.setAttribute('aria-busy', busy ? 'true' : 'false');
    }
  }

  function hydrateEmailPreferences() {
    var section = document.querySelector('[data-email-preferences-url]');
    if (!section) {
      return;
    }

    var preferencesUrl = section.getAttribute('data-email-preferences-url');
    if (!preferencesUrl) {
      return;
    }

    setEmailPreferencesDisabled(true);
    fetch(preferencesUrl, {
      method: 'GET',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
      },
    })
      .then(function(response) {
        if (!response.ok) {
          throw new Error('Email preferences fetch failed');
        }
        return response.json();
      })
      .then(function(data) {
        var preferences = data.preferences || {};
        emailPreferenceInputs().forEach(function(input) {
          if (Object.prototype.hasOwnProperty.call(preferences, input.name)) {
            input.checked = Boolean(preferences[input.name]);
          }
        });
        setEmailPreferencesStatus('Email subscriptions loaded.', false);
        setEmailPreferencesDisabled(false);
      })
      .catch(function(error) {
        setEmailPreferencesStatus(
          'Email preferences are temporarily unavailable. Try again later.',
          false
        );
        setEmailPreferencesDisabled(true);
        console.error('Error loading email preferences:', error);
      });
  }

  // UX-02: every immediate toggle owns a status region.  It is inserted
  // after the row (never inside it: the rows are labels, and a click on
  // text inside a label would flip the switch) and tied to the input with
  // aria-describedby, so screen readers hear Saving/Saved/not-saved.
  function statusRegionFor(row, input) {
    var knownId = row.getAttribute('data-toggle-status-id');
    var existing = knownId ? document.getElementById(knownId) : null;
    if (existing) {
      return existing;
    }
    var status = document.createElement('p');
    status.className = 'toggle-status';
    status.id = (input.id || 'toggle') + '-status';
    status.setAttribute('role', 'status');
    row.insertAdjacentElement('afterend', status);
    row.setAttribute('data-toggle-status-id', status.id);
    var describedBy = input.getAttribute('aria-describedby');
    input.setAttribute(
      'aria-describedby',
      describedBy ? describedBy + ' ' + status.id : status.id
    );
    return status;
  }

  function announce(status, message, busy) {
    status.textContent = message;
    status.setAttribute('aria-busy', busy ? 'true' : 'false');
  }

  function privacyStatus(row, previousValue) {
    // A privacy control must say what state still holds after a failed
    // save: silent revert left members believing a hidden record was
    // already hidden (audit UX-02).  The row's data attributes carry the
    // two states in the row's own words.
    var attribute = previousValue ? 'data-toggle-on-text' : 'data-toggle-off-text';
    var stillText = row.getAttribute(attribute);
    return stillText
      ? 'Your change was not saved. ' + stillText
      : 'Your change was not saved. Try again.';
  }

  function persistToggle(row, input, status) {
    // Disabling the input during the request is what keeps exactly one
    // in-flight request per toggle: a browser cannot fire another change
    // event on a disabled control, and the request here is never retried
    // behind the user's back.
    if (input.disabled) {
      return;
    }
    var previousValue = !input.checked;
    var toggleUrl = row.getAttribute('data-toggle-url');
    var failed = false;
    input.disabled = true;
    input.setAttribute('aria-busy', 'true');
    announce(status, 'Saving…', true);

    var formData = new FormData();
    formData.append('field', input.name);
    formData.append('value', input.checked ? 'true' : 'false');

    fetch(toggleUrl, {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrfToken,
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: formData,
    })
      .then(function(response) {
        // A signed-out session's redirect lands on the login page with a
        // 200 and an HTML body; only JSON responses count as outcomes, and
        // the two failure kinds get different messages below.
        var contentType = response.headers.get('content-type') || '';
        if (!response.ok || contentType.indexOf('application/json') === -1) {
          var error = new Error('Toggle update failed');
          error.sessionEnded =
            response.status === 401 ||
            response.status === 403 ||
            response.redirected;
          throw error;
        }
        return response.json();
      })
      .then(function(data) {
        // The server's answer is the state, not an echo of the request.
        if (Object.prototype.hasOwnProperty.call(data, 'value')) {
          input.checked = Boolean(data.value);
        }
        if (data.field === 'dark_mode') {
          // The account is authoritative while signed in: the server already
          // stored it, and this repaints the page the member changed it on so
          // the theme does not wait for the next navigation.
          window.applyDarkModePreference?.(data.value);
          // The signed-out mechanism (localStorage['darkMode'], read by the
          // pre-paint bootstrap only when data-authenticated is not "true") is
          // refreshed to the same value, so logging out does not flip the
          // theme.  It can never fight the account: nothing reads this key
          // while a session is signed in.
          localStorage.setItem('darkMode', data.value.toString());
        }
        announce(status, 'Saved.', false);
      })
      .catch(function(error) {
        input.checked = previousValue;
        failed = true;
        if (error && error.sessionEnded) {
          announce(
            status,
            'Your session has ended, so this change was not saved. ' +
              'Sign in again, then use the switch once more.',
            false
          );
        } else if (row.hasAttribute('data-privacy-toggle')) {
          announce(status, privacyStatus(row, previousValue), false);
        } else {
          announce(status, 'Your change was not saved. Try again.', false);
        }
        if (input.classList.contains('js-email-preference-toggle')) {
          setEmailPreferencesStatus(
            'Email preferences are temporarily unavailable. Try again later.',
            false
          );
        }
      })
      .finally(function() {
        input.disabled = false;
        input.removeAttribute('aria-busy');
        // No console diagnostics here on purpose: the visible, announced
        // status region is the failure record, and it names no account or
        // profile value.  The re-enabled switch is the retry, and focus
        // only sticks once the control is enabled again.
        if (failed) {
          input.focus();
        }
      });
  }

  document.querySelectorAll('.js-immediate-toggle-row').forEach(function(row) {
    var input = row.querySelector('input[type="checkbox"]');
    if (!input || !row.getAttribute('data-toggle-url')) {
      return;
    }

    var status = statusRegionFor(row, input);
    input.addEventListener('change', function() {
      persistToggle(row, input, status);
    });
  });

  hydrateEmailPreferences();
});

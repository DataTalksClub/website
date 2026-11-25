/* The Academy pages' forms: the waitlist, the free sessions and their QR
   recording form, and the double opt-in confirmation.

   Each form posts from the browser straight to the Academy signup backend, the
   same AWS Lambda the retired Astro site used, with exactly the request bodies
   that site sent: the same endpoints (/signup, /confirm), the same field keys
   and values, the consent version, the campaign parameters, referrer, page and
   browser language.  Every visible word is read from the page, which renders
   the synced Academy copy; this file holds only the request contract.

   What it deliberately no longer does: the retired site also sent a /pv page
   view beacon and Umami custom events.  The site's shared analytics consent
   (core/analytics_preferences.js) owns measurement now, so neither is sent,
   and nothing here ever reads or forwards a confirmation token anywhere but
   the one /confirm request.  No cookies, no storage. */
(function () {
  'use strict';

  var BASE = '/academy';
  var SOURCE = 'layoff_proof';
  var UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];
  var EMAIL_RE = /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/;
  /* The survey is a Google Form; these are its prefill fields for the track
     and the email address. */
  var SURVEY_TRACK_FIELD = 'entry.1984241375';
  var SURVEY_EMAIL_FIELD = 'entry.1840392648';

  function all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function one(selector, root) {
    return (root || document).querySelector(selector);
  }

  function utm() {
    var query = new URLSearchParams(window.location.search);
    var out = {};
    UTM_KEYS.forEach(function (key) {
      var value = query.get(key);
      if (value) {
        out[key] = value;
      }
    });
    return out;
  }

  function pagePath() {
    var path = window.location.pathname;
    return path.indexOf(BASE) === 0 ? path.slice(BASE.length) || '/' : path;
  }

  function referrer() {
    if (!document.referrer) {
      return '';
    }
    try {
      var url = new URL(document.referrer);
      return url.origin + url.pathname;
    } catch (error) {
      return '';
    }
  }

  function assign(target, source) {
    Object.keys(source).forEach(function (key) {
      target[key] = source[key];
    });
    return target;
  }

  function post(api, path, body) {
    if (!api) {
      return Promise.resolve(false);
    }
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, 15000) : null;
    return fetch(api.replace(/\/+$/, '') + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller ? controller.signal : undefined
    })
      .then(function (response) {
        if (response.status !== 200) {
          return false;
        }
        return response.json().then(
          function (data) { return !!data && data.ok === true; },
          function () { return false; }
        );
      })
      .catch(function () { return false; })
      .then(function (ok) {
        if (timer) {
          clearTimeout(timer);
        }
        return ok;
      });
  }

  function busy(button, on) {
    if (!button.dataset.idleLabel) {
      button.dataset.idleLabel = button.textContent;
    }
    button.disabled = on;
    button.textContent = on ? button.dataset.pendingLabel || button.dataset.idleLabel : button.dataset.idleLabel;
  }

  /* 1. The home page shows its two programs in a random order, as the
        retired site did, so neither is always first. */
  var tracks = one('[data-tracks]');
  if (tracks && tracks.children.length > 1 && Math.random() < 0.5) {
    tracks.appendChild(tracks.firstElementChild);
  }

  /* 2. Campaign parameters follow the reader to the other Academy pages. */
  (function () {
    var campaign = utm();
    if (!Object.keys(campaign).length) {
      return;
    }
    all('a[href]').forEach(function (link) {
      var raw = link.getAttribute('href') || '';
      if (raw.charAt(0) === '#' || /^(mailto|tel):/.test(raw) || link.hasAttribute('download')) {
        return;
      }
      var target;
      try {
        target = new URL(raw, window.location.href);
      } catch (error) {
        return;
      }
      if (target.origin !== window.location.origin || target.pathname.indexOf(BASE + '/') !== 0) {
        return;
      }
      Object.keys(campaign).forEach(function (key) {
        if (!target.searchParams.has(key)) {
          target.searchParams.set(key, campaign[key]);
        }
      });
      link.setAttribute('href', target.pathname + target.search + target.hash);
    });
  })();

  /* 3. The waitlist (home and program pages).  A signup in one card shows the
        confirmation note in every card, as the retired site did. */
  var signups = all('[data-academy-signup]');
  var joined = { email: '', track: '' };

  function surveyOption(card, track) {
    var option = one('select[name="program"] option[value="' + track + '"]', card);
    return option ? option.dataset.surveyOption || '' : card.dataset.surveyOption || '';
  }

  function showJoined(from) {
    signups.forEach(function (card) {
      one('form', card).hidden = true;
      var success = one('[data-success]', card);
      success.hidden = false;
      var survey = one('[data-survey]', success);
      var base = card.dataset.surveyUrl || '';
      if (survey && base) {
        survey.href = base + '&' + SURVEY_TRACK_FIELD + '=' + encodeURIComponent(surveyOption(card, joined.track)) +
          '&' + SURVEY_EMAIL_FIELD + '=' + encodeURIComponent(joined.email);
      }
    });
    var title = one('[data-success-title]', from);
    if (title) {
      title.focus();
    }
  }

  signups.forEach(function (card) {
    var form = one('form', card);
    var api = card.dataset.apiOrigin || '';
    var nameInput = one('input[name="name"]', form);
    var payment = one('select[name="payment_plan"]', form);
    var email = one('input[name="email"]', form);
    var honeypot = one('input[name="website"]', form);
    var button = one('button[type="submit"]', form);
    var paymentError = one('[data-error="payment_plan"]', form);
    var invalidError = one('[data-error="invalid"]', form);
    var failedError = one('[data-error="failed"]', form);
    /* Disabled in the markup so a submit without this script cannot put the
       address into the URL. */
    button.disabled = false;

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      failedError.hidden = true;
      var address = email.value.trim();
      var plan = payment ? payment.value : '';
      email.value = address;
      var emailOk = email.checkValidity() && EMAIL_RE.test(address);
      var paymentOk = !payment || !!plan;
      if (payment) {
        paymentError.hidden = paymentOk;
        if (paymentOk) {
          payment.removeAttribute('aria-invalid');
        } else {
          payment.setAttribute('aria-invalid', 'true');
        }
      }
      if (!emailOk) {
        invalidError.hidden = false;
        email.setAttribute('aria-invalid', 'true');
        email.focus();
        return;
      }
      invalidError.hidden = true;
      email.removeAttribute('aria-invalid');
      if (!paymentOk) {
        payment.focus();
        return;
      }
      var program = one('select[name="program"]', form);
      var track = program ? program.value : card.dataset.track || '';
      busy(button, true);
      var body = assign(
        { email: address, name: nameInput ? nameInput.value.trim() : '', track: track, payment_plan: plan },
        utm()
      );
      assign(body, {
        referrer: referrer(),
        page: pagePath(),
        lang: navigator.language,
        consent_version: card.dataset.consentVersion,
        website: honeypot.value
      });
      post(api, '/signup', body).then(function (ok) {
        busy(button, false);
        if (ok) {
          joined.email = address;
          joined.track = track;
          showJoined(card);
        } else {
          failedError.hidden = false;
        }
      });
    });

    if (payment) {
      payment.addEventListener('change', function () {
        if (payment.value) {
          payment.removeAttribute('aria-invalid');
          paymentError.hidden = true;
        }
      });
    }

    email.addEventListener('input', function () {
      if (email.getAttribute('aria-invalid') && EMAIL_RE.test(email.value.trim())) {
        email.removeAttribute('aria-invalid');
        invalidError.hidden = true;
      }
    });

    /* The optional quick questions: each answer is its own /signup post, and
       an answer shows as chosen only once it is stored. */
    var answersError = one('[data-error="answers"]', card);
    all('[data-group]', card).forEach(function (chip) {
      chip.addEventListener('click', function () {
        var group = chip.dataset.group;
        var peers = all('[data-group="' + group + '"]', card);
        all('[data-error="answers"]').forEach(function (node) { node.hidden = true; });
        peers.forEach(function (peer) { peer.disabled = true; });
        var answers = {};
        answers[group] = chip.dataset.value;
        post(api, '/signup', { email: joined.email, track: joined.track, answers: answers }).then(function (ok) {
          peers.forEach(function (peer) { peer.disabled = false; });
          if (ok) {
            peers.forEach(function (peer) {
              peer.setAttribute('aria-pressed', String(peer.dataset.value === chip.dataset.value));
            });
          } else if (answersError) {
            answersError.hidden = false;
          }
        });
      });
    });
  });

  /* 4. The free sessions (source "layoff_proof"): the QR recording form.
        Registering for a session happens on its event page, not here. */
  function checked(form, name) {
    var input = one('input[name="' + name + '"]:checked', form);
    return input ? input.value : '';
  }

  all('[data-academy-sessions]').forEach(function (card) {
    var kind = 'qr';
    var api = card.dataset.apiOrigin || '';
    var form = one('form', card);
    var email = one('input[name="email"]', form);
    var honeypot = one('input[name="website"]', form);
    var button = one('button[type="submit"]', form);
    var invalidError = one('[data-error="invalid"]', form);
    var failedError = one('[data-error="failed"]', form);
    var success = one('[data-success]', card);
    var known = (card.dataset.sessionIds || '').split(/\s+/).filter(Boolean);
    var qrParam = new URLSearchParams(window.location.search).get('session') || '';
    var qrSession = /^[0-9]+$/.test(qrParam) && known.indexOf('session-' + qrParam) !== -1 ? 'session-' + qrParam : '';
    button.disabled = false;

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      failedError.hidden = true;
      var address = email.value.trim();
      email.value = address;
      if (!email.checkValidity() || !EMAIL_RE.test(address)) {
        invalidError.hidden = false;
        email.setAttribute('aria-invalid', 'true');
        email.focus();
        return;
      }
      invalidError.hidden = true;
      email.removeAttribute('aria-invalid');
      if (!qrSession) {
        failedError.hidden = false;
        return;
      }
      busy(button, true);
      var optin = one('input[name="academy_optin"]', form);
      var name = one('input[name="name"]', form);
      var body = assign(
        {
          source: SOURCE,
          form: kind,
          email: address,
          name: name ? name.value.trim() : '',
          status: checked(form, 'status'),
          academy_optin: optin && optin.checked ? 'yes' : ''
        },
        utm()
      );
      assign(body, {
        referrer: referrer(),
        page: pagePath(),
        lang: navigator.language,
        consent_version: card.dataset.consentVersion,
        website: honeypot.value
      });
      body.session = qrSession;
      post(api, '/signup', body).then(function (ok) {
        busy(button, false);
        if (!ok) {
          failedError.hidden = false;
          return;
        }
        form.hidden = true;
        success.hidden = false;
        var title = one('[data-success-title]', success);
        if (title) {
          title.focus();
        }
      });
    });

    email.addEventListener('input', function () {
      if (email.getAttribute('aria-invalid') && EMAIL_RE.test(email.value.trim())) {
        email.removeAttribute('aria-invalid');
        invalidError.hidden = true;
      }
    });
  });

  /* 5. The double opt-in confirmation. */
  var confirmCard = one('[data-academy-confirm]');
  if (confirmCard) {
    var copy = confirmCard.dataset;
    var titleNode = one('[data-confirm-title]');
    var message = one('[data-confirm-message]', confirmCard);
    var back = one('[data-confirm-back]', confirmCard);
    var programs = (copy.programs || '').split(/\s+/).filter(Boolean);
    var root = copy.root || BASE + '/';

    var backPath = function (data) {
      if (data && data.source === SOURCE) {
        return root + 'sessions/';
      }
      if (data && programs.indexOf(String(data.track || '')) !== -1) {
        return root + data.track + '/';
      }
      return root;
    };

    var show = function (title, text, path) {
      if (titleNode) {
        titleNode.textContent = title;
        titleNode.focus();
      }
      message.textContent = text;
      back.setAttribute('href', path || root);
    };

    var token = new URLSearchParams(window.location.search).get('token') || '';
    var api = (copy.apiOrigin || '').replace(/\/+$/, '');
    if (!token) {
      show(copy.missingTitle, copy.missingMessage);
    } else if (!api) {
      show(copy.unconfiguredTitle, copy.unconfiguredMessage);
    } else {
      var controller = new AbortController();
      var timer = setTimeout(function () { controller.abort(); }, 15000);
      fetch(api + '/confirm?token=' + encodeURIComponent(token) + '&format=json', {
        method: 'GET',
        headers: { accept: 'application/json' },
        signal: controller.signal
      })
        .then(function (response) {
          return response.json().then(
            function (data) { return { ok: response.ok, data: data }; },
            function () { return { ok: response.ok, data: null }; }
          );
        })
        .then(function (result) {
          var data = result.data;
          if (result.ok && data && data.ok) {
            show(data.title || copy.confirmedTitle, data.message || copy.confirmedMessage, backPath(data));
          } else {
            show(
              (data && data.title) || copy.invalidTitle,
              (data && data.message) || copy.invalidMessage,
              backPath(data)
            );
          }
        })
        .catch(function () {
          show(copy.failedTitle, copy.failedMessage);
        })
        .then(function () {
          clearTimeout(timer);
        });
    }
  }
})();

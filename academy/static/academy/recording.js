/* A session recap's recording: a click-to-play frame (academy/_session_media.html).
   Nothing is requested from YouTube until the reader presses play; the
   youtube-nocookie player is built then and takes the facade's place. */
(function () {
  var frame = document.querySelector('[data-video-embed]');
  var facade = frame && frame.querySelector('[data-video-facade]');
  if (!facade) {
    return;
  }
  facade.setAttribute('role', 'button');

  function play(event) {
    event.preventDefault();
    var player = document.createElement('iframe');
    player.src = frame.getAttribute('data-video-embed') + '&autoplay=1';
    player.title = frame.getAttribute('data-video-title');
    player.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
    player.referrerPolicy = 'strict-origin-when-cross-origin';
    player.allowFullscreen = true;
    facade.replaceWith(player);
    player.focus();
  }

  facade.addEventListener('click', play);
  facade.addEventListener('keydown', function (event) {
    if (event.key === ' ') {
      play(event);
    }
  });
})();

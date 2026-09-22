// Credit story player for React web apps: embeds /player/ in an <iframe> and talks to it with
// postMessage. Copy this file into your app; it has no dependencies besides React.
//
//   const player = useRef(null);
//   <div style={{ width: 390, height: 844 }}>
//     <CreditStoryPlayer
//       ref={player}
//       baseUrl="https://your-backend.example.com"
//       storyUrl={storyUrlFromApi}            // story_url from POST /api/story/crif
//       onEvent={(e) => console.log(e.type, e)}
//       onClose={() => setOpen(false)}
//     />
//   </div>
//   player.current.pause();  player.current.seek(120);
//
// Leave storyUrl empty to show the player's loader straight away, then call
// player.current.load(storyUrl) when your API call returns.
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';

const SOURCE = 'credit-story-player';

const CreditStoryPlayer = forwardRef(function CreditStoryPlayer(
  { baseUrl, storyUrl, theme = 'dark', autoplay = false, showClose = true, onEvent, onClose,
    title = 'Credit report video', className, style },
  ref,
) {
  const frame = useRef(null);
  const ready = useRef(false);
  const queue = useRef([]);
  const handlers = useRef({ onEvent, onClose });
  handlers.current = { onEvent, onClose };

  const base = baseUrl.replace(/\/+$/, '');
  const origin = useMemo(() => new URL(base, window.location.href).origin, [base]);
  const src = useMemo(() => {
    const url = new URL(`${base}/player/`, window.location.href);
    url.searchParams.set('embed', '1');
    url.searchParams.set('theme', theme);
    url.searchParams.set('origin', window.location.origin);   // the player only posts to this page
    if (storyUrl) url.searchParams.set('story', storyUrl);
    if (autoplay) url.searchParams.set('autoplay', '1');
    if (!showClose) url.searchParams.set('close', '0');
    return url.href;
  }, [base, theme, storyUrl, autoplay, showClose]);

  // A new src reloads the player: commands wait for its 'ready' event.
  useEffect(() => { ready.current = false; }, [src]);

  useEffect(() => {
    const listen = (e) => {
      if (e.origin !== origin || !frame.current || e.source !== frame.current.contentWindow) return;
      const msg = e.data;
      if (!msg || msg.source !== SOURCE) return;
      if (msg.type === 'ready') {
        ready.current = true;
        queue.current.splice(0).forEach((m) => frame.current.contentWindow.postMessage(m, origin));
      }
      if (handlers.current.onEvent) handlers.current.onEvent(msg);
      if (msg.type === 'close' && handlers.current.onClose) handlers.current.onClose();
    };
    window.addEventListener('message', listen);
    return () => window.removeEventListener('message', listen);
  }, [origin]);

  useImperativeHandle(ref, () => {
    const send = (command, extra = {}) => {
      const msg = { target: SOURCE, command, ...extra };
      if (ready.current && frame.current) frame.current.contentWindow.postMessage(msg, origin);
      else queue.current.push(msg);
    };
    return {
      play: () => send('play'),
      pause: () => send('pause'),
      toggle: () => send('toggle'),
      seek: (seconds) => send('seek', { time: seconds }),
      load: (url, play = true) => send('load', { url, autoplay: play }),
      setLanguage: (code) => send('language', { code }),
      setCaptions: (on) => send('captions', { on }),
      requestState: () => send('state'),   // answered with a 'state' event
    };
  }, [origin]);

  return (
    <iframe
      ref={frame}
      src={src}
      title={title}
      className={className}
      allow="autoplay; fullscreen"
      style={{ display: 'block', width: '100%', height: '100%', border: 0, ...style }}
    />
  );
});

export default CreditStoryPlayer;

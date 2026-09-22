// Story player settings.
//   storyUrl  Story JSON that plays when the page opens (relative to index.html, or absolute).
//             Override it for one visit with a query param:  index.html?story=data/story.en.json
//   apiBase   Where the backend runs (POST /api/story). Empty means the same server as this page,
//             which is the case at http://localhost:8000/player/. If you serve this folder some
//             other way, set it to the backend, e.g. 'http://localhost:8000'.
//   theme     'dark' (default) or 'light'. Override for one visit with ?theme=light.
// Audio paths inside a story JSON resolve relative to the JSON file's own URL.
window.STORY_PLAYER_CONFIG = {
  storyUrl: 'data/story.hi.json',
  apiBase: '',
  theme: 'dark',
};

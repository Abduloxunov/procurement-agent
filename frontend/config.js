// Where the backend lives.
//
// Local development: leave empty. FastAPI serves index.html itself, so the
// frontend and API share an origin and relative paths just work.
//
// Vercel: set this to your GCP VM, including the scheme and no trailing
// slash, e.g.
//     window.API_BASE = "http://34.116.22.9:8000";
//
// If the page is served over https and this is http, the browser blocks the
// requests as mixed content. Put the VM behind https, or open the Vercel
// deployment over http while testing.
window.API_BASE = "";

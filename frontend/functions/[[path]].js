// Cloudflare Pages Function: the friendly front door for the app.
//
// The HTML, /config, /auth/token and the JWT-guarded API are all served by the
// same API Gateway host. Proxying every request keeps the browser on one origin,
// so the sessionStorage token, the Cognito redirect back to "/" and the
// same-origin fetches in app/static/index.html keep working unchanged. It also
// means no CORS: the browser only ever talks to this host.
//
// If the HTTP API is ever recreated, update API_ORIGIN from
// `terraform -chdir=infra output -raw api_url`.
const API_ORIGIN = "https://uod7n5ou71.execute-api.us-east-1.amazonaws.com";

// Hop-by-hop headers, plus the ones Cloudflare's fetch already decoded: passing
// them on would describe a body the browser no longer receives.
const DROP_HEADERS = [
  "connection",
  "content-encoding",
  "content-length",
  "transfer-encoding",
];

export async function onRequest({ request }) {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  headers.delete("host");

  const upstream = await fetch(API_ORIGIN + url.pathname + url.search, {
    method: request.method,
    headers,
    body:
      request.method === "GET" || request.method === "HEAD"
        ? undefined
        : request.body,
    // Pass Cognito's sign-in redirect through instead of following it here.
    redirect: "manual",
  });

  const responseHeaders = new Headers(upstream.headers);
  for (const name of DROP_HEADERS) responseHeaders.delete(name);

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

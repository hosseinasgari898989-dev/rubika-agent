# Authorized YouTube relay (optional Render service)

## Purpose
A small, authenticated relay for downloading a **public YouTube video that the project owner has permission to download and reuse**. It is separate from the Cloudflare control Worker and from the Rubika publishing code.

The relay is a normal downloader, not a proxy that defeats YouTube access controls. It intentionally does not use account cookies, login credentials, external proxy services, challenge solvers, DRM workarounds, or hidden APIs. If YouTube says the video requires sign-in or is unavailable to the runner, the request ends with a generic `source_restricted_or_unavailable` error. Do not try to bypass that restriction.

## API
- `GET /health`: reports service health and confirms publishing is disabled.
- `POST /v1/download`: requires `Authorization: Bearer <RELAY_API_TOKEN>` and JSON:

  ```json
  {
    "url": "https://www.youtube.com/shorts/VN5KSQ4didU",
    "platform": "youtube",
    "permission_confirmed": true
  }
  ```

The client must explicitly confirm permission. This field is a workflow guard, not proof of copyright ownership. Only YouTube watch URLs, Shorts URLs and youtu.be links are accepted; playlist URLs, non-YouTube hosts, HTTP links and credentials embedded in URLs are rejected.

The service limits downloads to 180 seconds and 45 MiB, validates the video with ffprobe, and remuxes/transcodes to H.264/AAC MP4 when needed. Temporary files are removed after the response. Publishing to Rubika is not implemented here and remains disabled.

## Render configuration
`render.yaml` and `relay/Dockerfile` describe a free Docker web service for an optional trial. A free service can sleep when idle and has ephemeral storage; it is not a permanent archive. MEGA remains the durable file store, and D1 stores metadata only.

When the owner chooses to deploy the service:
1. Create a Render web service from this repository and its `render.yaml` Blueprint.
2. Set the secret `RELAY_API_TOKEN` through Render's secret environment-variable UI. Use a randomly generated value of at least 32 characters; never commit it or paste it into chat.
3. Wait for `/health` to return `ok: true`.
4. First test `/v1/download` with a single video the owner created or is licensed/authorized to download. The test request must use the API token as a protected header and set `permission_confirmed: true`.

No Render service is deployed or configured by this repository change. No real video was downloaded by the relay tests; those tests use mocked media. The relay does not store files persistently and does not publish to Rubika.

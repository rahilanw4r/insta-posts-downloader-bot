# Public Instagram Profile Archive Website (MVP)

This adds a simple web interface to this repository. A user enters a public Instagram username/profile URL, starts a background job, and downloads the collected accessible posts as a ZIP to their own device.

## Run in GitHub Codespaces

```bash
git pull origin main
python -m pip install -r webapp/requirements.txt
python webapp/app.py
```

Open port 8080 in the Codespaces Ports tab and visit the forwarded URL.

## Important limitations

- This is an experimental MVP, not a guarantee of a complete archive. Instagram can throttle/block automated access, and posts may fail or be unavailable.
- Private profiles are rejected. No Instagram password, session cookie, or access-control bypass is implemented.
- The server needs enough disk space and memory for the entire ZIP. Use a persistent server/container for real deployments; Codespaces is for testing and may stop.
- Job state and archives are stored on the server temporarily and are lost when the process/container stops. Add durable object storage, job persistence, rate limits, per-user quotas, expiry cleanup, and HTTPS before public launch.
- Only download media you own or have permission to save. Respect Instagram's terms and applicable copyright/privacy laws.
- This MVP is synchronous at the worker level and uses an in-memory job registry. Run a single app process for testing; production needs a proper queue and shared job store.
